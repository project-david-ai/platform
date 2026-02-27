from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

import jwt
from projectdavid_common.utilities.tool_validator import ToolValidator
from projectdavid_common.validation import StatusEnum

# --- DEPENDENCIES ---
from entities_api.platform_tools.handlers.computer.shell_command_interface import (
    run_shell_commands_async,
)
from src.api.entities_api.services.logging_service import LoggingUtility

LOG = LoggingUtility()


class ShellExecutionMixin:
    """
    Executes POSIX‑style shell commands inside the Project‑David sandbox asynchronously.

    Level 2 Enhancement: Automated self-correction for shell failures (missing binaries,
    path errors, or command syntax issues).
    """

    @staticmethod
    def _format_level2_shell_error(error_content: str) -> str:
        """
        Translates raw shell stderr or execution crashes into actionable hints for the LLM.
        """
        return (
            f"Shell Execution Failed:\n{error_content}\n\n"
            "Instructions: Please analyze the error output above. If a command was 'not found', "
            "verify the binary name or check if it needs to be installed. If a path is incorrect, "
            "list the directory contents first. Correct your commands and retry execution."
        )

    def _generate_shell_auth_token(self, subject_id: str, room_id: str) -> str:
        """
        Generates a short-lived JWT to authorize the connection to the Sandbox API.
        """
        secret = os.getenv("SANDBOX_AUTH_SECRET")
        if not secret:
            LOG.error(
                "CRITICAL: SANDBOX_AUTH_SECRET is missing in environment variables."
            )
            raise ValueError("Server configuration error: Sandbox secret missing.")

        payload = {
            "sub": subject_id,
            "room": room_id,
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "scopes": ["execution", "shell"],
        }

        return jwt.encode(payload, secret, algorithm="HS256")

    async def handle_shell_action(
        self,
        thread_id: str,
        run_id: str,
        assistant_id: str,
        arguments_dict: Dict[str, Any],
        tool_call_id: Optional[str] = None,
        decision: Optional[Dict] = None,
    ) -> None:
        """
        Asynchronous handler for shell commands.
        Executes commands, performs validation, and submits output.
        """

        tool_name = "computer"
        LOG.info("ShellExecutionMixin: started for run_id=%s", run_id)

        # --- SHARED INPUT VALIDATION ---
        validator = ToolValidator()
        validator.schema_registry = {tool_name: ["commands"]}

        validation_error = validator.validate_args(tool_name, arguments_dict)
        is_valid = validation_error is None

        if not is_valid:
            LOG.warning(f"ShellExecution ▸ Validation Failed: {validation_error}")

            # Attempt to create a failed action record for history
            try:
                action = await asyncio.to_thread(
                    self.project_david_client.actions.create_action,
                    tool_name=tool_name,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    function_args=arguments_dict,
                    decision=decision,
                )
            except Exception:
                action = None

            error_msg = (
                f"{validation_error}\n"
                "Please correct the function arguments and try again."
            )

            await self.submit_tool_output(
                thread_id=thread_id,
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                content=error_msg,
                action=action,
                is_error=True,
            )
            return

        # --- CREATE ACTION ---
        try:
            action = await asyncio.to_thread(
                self.project_david_client.actions.create_action,
                tool_name=tool_name,
                run_id=run_id,
                tool_call_id=tool_call_id,
                function_args=arguments_dict,
                decision=decision,
            )
        except Exception as e:
            LOG.error(f"ShellExecution ▸ Action creation failed: {e}")
            return

        commands: List[str] = arguments_dict.get("commands", [])
        if not commands:
            await self.submit_tool_output(
                thread_id=thread_id,
                assistant_id=assistant_id,
                tool_call_id=tool_call_id,
                content="No commands provided.",
                action=action,
            )
            return

        accumulated_content = ""
        execution_had_error = False

        # --- EXECUTE SHELL COMMANDS ---
        try:
            auth_token = self._generate_shell_auth_token(
                subject_id=f"run_{run_id}",
                room_id=thread_id,
            )

            async for chunk in run_shell_commands_async(
                commands, thread_id=thread_id, token=auth_token
            ):
                accumulated_content += chunk

                # Detect shell errors
                if any(
                    err_marker in chunk.lower()
                    for err_marker in [
                        "not found",
                        "permission denied",
                        "error:",
                        "no such file",
                    ]
                ):
                    execution_had_error = True

        except Exception as e:
            execution_had_error = True
            LOG.error(f"ShellExecution ▸ Exception during shell execution: {e}")

        # --- FINALIZE OUTPUT ---
        if execution_had_error:
            final_content = self._format_level2_shell_error(
                accumulated_content or "Unknown shell failure."
            )
            final_state = True  # indicates error
        else:
            final_content = (
                accumulated_content.strip()
                or "Command executed successfully with no output."
            )
            final_state = False

        await self.submit_tool_output(
            thread_id=thread_id,
            assistant_id=assistant_id,
            tool_call_id=tool_call_id,
            content=final_content,
            action=action,
            is_error=final_state,
        )

        # Update DB Action Status
        await asyncio.to_thread(
            self.project_david_client.actions.update_action,
            action_id=action.id,
            status=(
                StatusEnum.completed.value
                if not execution_had_error
                else StatusEnum.failed.value
            ),
        )
