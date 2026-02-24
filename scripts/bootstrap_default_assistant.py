# entities_api/services/assistant_setup_service.py
import argparse
import sys

# --- Path Setup (Example - uncomment/adjust if needed) ---
# Ensure project root is in path to find projectdavid, etc.
# project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")) # Adjust levels as needed
# if project_root not in sys.path:
#    sys.path.insert(0, project_root)
# --- End Path Setup ---

try:
    from projectdavid import Entity
    from projectdavid_common import ValidationInterface
    from projectdavid_common.constants.assistant_map import PLATFORM_ASSISTANT_ID_MAP
    from projectdavid_common.constants.tools import TOOLS_ID_MAP

    from entities_api.constants.assistant import BASE_TOOLS, DEFAULT_MODEL
    from entities_api.orchestration.instructions.assembler import assemble_instructions
    from entities_api.platform_tools.definitions import assemble_tools
    from entities_api.services.logging_service import LoggingUtility
except ImportError as e:
    print(f"Error importing required modules: {e}")
    print(
        "Please ensure 'projectdavid', 'projectdavid_common', and local 'entities_api' modules are accessible."
    )
    print(
        "You might need to adjust PYTHONPATH or run the script from the correct directory."
    )
    sys.exit(1)


# --- Initialize necessary components ---
validate = ValidationInterface()
logging_utility = (
    LoggingUtility()
)  # Instantiate logger for use in main block too if needed


class AssistantSetupService:
    # Modified __init__ to accept an initialized client
    def __init__(self, client: Entity):
        """
        Initializes the service with a pre-configured API client.

        Args:
            client: An initialized instance of the projectdavid.Entity client.
        """
        if not isinstance(client, Entity):
            raise TypeError(
                "AssistantSetupService requires an initialized projectdavid.Entity client."
            )
        self.client = client
        # Use the globally initialized logger or create a new one per instance
        self.logging_utility = logging_utility

    # ------------------------------------------------------------------ #
    #  Tools
    # ------------------------------------------------------------------ #
    def create_and_associate_tools(
        self,
        function_definitions: list[dict],
        assistant_id: str,
    ) -> None:
        """
        For each function spec in `function_definitions`…

        • if a RESERVED tool (in TOOLS_ID_MAP) already exists → just associate
        • if it does **not** exist  → create it **with the canonical ID**
        • if it's a completely new tool                     → create it normally

        All operations are idempotent: re-running the script won’t duplicate rows
        or raise “already associated” errors.
        """
        created, associated, skipped = 0, 0, 0

        for func in function_definitions:
            name = func.get("function", {}).get("name")
            if not name:
                self.logging_utility.warning("Skipping anonymous tool definition.")
                skipped += 1
                continue

            canonical_id = TOOLS_ID_MAP.get(name)  # None for non-reserved tools
            tool_id = canonical_id  # may stay None

            # ── 1. try to fetch by ID (fast path for reserved) ───────────
            if canonical_id:
                try:
                    tool = self.client.tools.get_tool_by_id(canonical_id)
                    self.logging_utility.debug(
                        "Found reserved tool %s (%s)", name, canonical_id
                    )
                except Exception:
                    tool = None

            # ── 2. else / fallback : fetch by name  ───────────────────────
            if not canonical_id or tool is None:
                try:
                    tool = self.client.tools.get_tool_by_name(name)
                    tool_id = tool.id
                    self.logging_utility.debug(
                        "Found existing tool %s (%s) by name", name, tool_id
                    )
                except Exception:
                    tool = None  # genuinely missing

            # ── 3. create if still missing  ───────────────────────────────
            if tool is None:
                try:
                    validated_fn = validate.ToolFunction(function=func["function"])
                    payload = {
                        "name": name,
                        "type": "function",
                        "function": validated_fn.model_dump(),
                    }
                    if canonical_id:  # force the reserved ID
                        payload["id"] = canonical_id
                    tool = self.client.tools.create_tool(**payload)
                    tool_id = tool.id
                    created += 1
                    self.logging_utility.info(
                        "Created tool %-22s  id=%s", name, tool_id
                    )
                except Exception as e:
                    self.logging_utility.error(
                        "Failed to create tool %s: %s", name, e, exc_info=True
                    )
                    continue

            # ── 4. associate with assistant (idempotent on server) ────────
            try:
                self.client.tools.associate_tool_with_assistant(
                    tool_id=tool_id, assistant_id=assistant_id
                )
                associated += 1
            except Exception as e:
                # If the API already stores the relation, just log & carry on
                if "already associated" in str(e).lower():
                    self.logging_utility.debug(
                        "Tool %s already linked to assistant %s", tool_id, assistant_id
                    )
                else:
                    self.logging_utility.error(
                        "Associate failed for tool %s → assistant %s: %s",
                        tool_id,
                        assistant_id,
                        e,
                        exc_info=True,
                    )

        self.logging_utility.info(
            "Tool sync done: %d created  |  %d linked/verified  |  %d skipped",
            created,
            associated,
            skipped,
        )

    def setup_assistant_with_tools(
        self,
        user_id,  # user_id is passed but might not be directly needed here if assistant isn't user-specific
        assistant_name,
        assistant_description,
        model,
        instructions,
        function_definitions,
    ):
        """
        Gets an existing assistant by a known ID ('default') or creates one,
        then ensures the specified tools are created and associated.
        """
        target_assistant_id = PLATFORM_ASSISTANT_ID_MAP["default_assistant"]
        assistant = None

        try:
            # Attempt to retrieve the assistant by its logical ID/alias
            assistant = self.client.assistants.retrieve_assistant(target_assistant_id)
            self.logging_utility.info(
                f"Found existing assistant '{assistant.name}' with logical ID '{target_assistant_id}' (Actual ID: {assistant.id})"
            )
            # Optionally update existing assistant details if needed (e.g., instructions, model)
            # self.client.assistants.update_assistant(assistant.id, instructions=instructions, model=model, ...)

        except Exception:  # Be more specific if possible (e.g., NotFoundError)
            # If retrieval fails (assistant not found by 'default' ID), create a new one.
            self.logging_utility.warning(
                f"Assistant with logical ID '{target_assistant_id}' not found. Creating a new one."
            )
            try:
                assistant = self.client.assistants.create_assistant(
                    name=assistant_name,
                    description=assistant_description,
                    model=model,
                    instructions=instructions,
                    tools=[
                        {"type": "code_interpreter"},
                        {"type": "web_search"},
                        {"type": "vector_store_search"},
                        {"type": "computer"},
                        {"type": "file_search"},
                    ],
                    assistant_id=target_assistant_id,
                )
                self.logging_utility.info(
                    f"Created new assistant: '{assistant.name}' (Logical ID: '{target_assistant_id}', Actual ID: {assistant.id})"
                )
            except Exception as e:
                self.logging_utility.error(
                    f"Failed to create assistant '{assistant_name}': {e}", exc_info=True
                )
                raise  # Re-raise critical creation failure

        # Ensure assistant object is valid before proceeding
        if not assistant or not hasattr(assistant, "id"):
            self.logging_utility.error("Failed to obtain a valid assistant object.")
            raise ValueError("Could not retrieve or create the target assistant.")

        # Now, create and associate tools for the obtained assistant ID
        self.create_and_associate_tools(function_definitions, assistant.id)
        return assistant

    def orchestrate_default_assistant(self, user_id: str):
        """
        Main orchestration flow for setting up the 'default' assistant.
        Now accepts the target user_id.

        Args:
            user_id: The ID of the user this operation is performed for/on behalf of.
                     (Note: The default assistant itself might be global or user-specific
                      depending on API design. This user_id might be for authorization
                      or context rather than ownership of the assistant).
        """
        self.logging_utility.info(
            f"Starting default assistant orchestration for user context: {user_id}"
        )
        try:
            # The setup_assistant_with_tools handles get-or-create of the *assistant*
            # It no longer handles user creation. We assume the provided user_id is valid
            # for the context of this operation (e.g., the API key belongs to this user
            # or an admin performing actions).

            # Assemble instructions dynamically if needed
            instructions = assemble_instructions()

            assistant = self.setup_assistant_with_tools(
                user_id=user_id,  # Pass user_id for context if needed by setup_assistant_with_tools
                assistant_name="Q",  # Default name for the assistant
                assistant_description="Default general-purpose assistant",  # Default description
                model=DEFAULT_MODEL,
                instructions=instructions,
                function_definitions=assemble_tools(),
            )

            self.logging_utility.info(
                f"Orchestration completed. Assistant ready (ID: {assistant.id})."
            )
            return assistant

        except Exception as e:
            self.logging_utility.critical(
                f"Critical failure in orchestration for user {user_id}: {e}",
                exc_info=True,
            )
            # Depending on desired behavior, either re-raise or return None/False
            raise  # Re-raise to indicate failure to the caller


# --- Main Execution Block ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Set up or verify the 'default' assistant and its tools. "
        "Accepts API key and User ID via arguments or interactive prompts.",
        formatter_class=argparse.RawTextHelpFormatter,  # Allow multiline help
    )
    parser.add_argument(
        "--api-key",
        help="The API key for authenticating. If omitted, you will be prompted.",
    )
    parser.add_argument(
        "--user-id",
        help="The User ID context for this operation. If omitted, you will be prompted.",
    )
    parser.add_argument(
        "--base_workers-url",
        default=None,  # Or set a default like "http://localhost:9000"
        help="Optional base_workers URL for the API endpoint.",
    )

    args = parser.parse_args()

    # --- Determine Input Values (Arguments > Prompt) ---
    api_key = args.api_key
    user_id = args.user_id
    base_url = args.base_url  # Remains optional

    # --- Prompt for missing required values ---
    if not api_key:
        print("API Key not provided via argument.")
        # --- MODIFICATION: Use input() instead of getpass() ---
        print("WARNING: API Key will be visible during input.")
        try:
            api_key = input(
                "Please enter your API key: "
            )  # Changed from getpass.getpass
            if not api_key:
                print("API Key cannot be empty. Exiting.", file=sys.stderr)
                sys.exit(1)
        except EOFError:
            # This error occurs if input is redirected and empty, or in some non-interactive environments
            print("\nError: Could not read API Key from input stream.", file=sys.stderr)
            print(
                "Hint: If running non-interactively, please provide the --api-key argument.",
                file=sys.stderr,
            )
            sys.exit(1)
        except Exception as e:  # Catch potential input issues
            print(f"\nError reading API key: {e}", file=sys.stderr)
            sys.exit(1)
        # --- END MODIFICATION ---

    if not user_id:
        print("User ID not provided via argument.")
        try:
            user_id = input("Please enter your User ID: ")
            if not user_id:
                print("User ID cannot be empty. Exiting.", file=sys.stderr)
                sys.exit(1)
        except EOFError:
            print("\nError: Could not read User ID from input stream.", file=sys.stderr)
            print(
                "Hint: If running non-interactively, please provide the --user-id argument.",
                file=sys.stderr,
            )
            sys.exit(1)
        except Exception as e:
            print(f"\nError reading User ID: {e}", file=sys.stderr)
            sys.exit(1)

    # --- Print Configuration Being Used ---
    print("\n--- Assistant Setup Configuration ---")
    print(f"User ID Context: {user_id}")
    # Still mask the key in the confirmation printout for safety
    print(f"API Key: {'*' * (len(api_key) - 4)}{api_key[-4:]}")
    if base_url:
        print(f"Base URL: {base_url}")
    else:
        print("Base URL: (Using client default)")
    print("-" * 35)

    # --- Initialize Client ---
    try:
        client_params = {"api_key": api_key}
        if base_url:
            client_params["base_url"] = base_url

        api_client = Entity(**client_params)
        # Optional: Add a quick check here like retrieving the user to validate credentials early
        try:
            print("Validating credentials by retrieving user...")
            retrieved_user = api_client.users.retrieve_user(user_id)
            print(
                f"Credentials seem valid (User '{getattr(retrieved_user, 'email', user_id)}' retrieved successfully)."
            )
        except Exception as check_err:
            print(
                f"\nWarning: Could not validate credentials by retrieving user {user_id}.",
                file=sys.stderr,
            )
            print(f"Error detail: {check_err}", file=sys.stderr)
            print(
                "Continuing execution, but API calls might fail if credentials are incorrect.",
                file=sys.stderr,
            )
            # Decide whether to exit or continue
            # sys.exit(1) # Uncomment to make validation mandatory

        print("API Client initialized.")

    except Exception as e:
        print(f"\nError: Failed to initialize API client: {e}", file=sys.stderr)
        logging_utility.error(f"Failed to initialize API client", exc_info=True)
        sys.exit(1)

    # --- Instantiate and Run Service ---
    try:
        # Pass the initialized client to the service
        service = AssistantSetupService(client=api_client)

        # Run the orchestration, passing the determined user ID
        print("\nStarting assistant orchestration...")
        logging_utility.info(
            f"Initiating orchestration via script execution (User: {user_id})"
        )
        assistant = service.orchestrate_default_assistant(user_id=user_id)

        if assistant:
            print("\n--- Orchestration Successful ---")
            print(f"Assistant Name: {getattr(assistant, 'name', 'N/A')}")
            print(f"Assistant ID:   {getattr(assistant, 'id', 'N/A')}")
            print("Tools should now be created/associated.")
        else:
            # This path might not be reached if orchestrate_default_assistant raises on failure
            print("\n--- Orchestration Finished (No Assistant Object Returned) ---")
            print("Check logs for details. There might have been an issue.")

    except Exception as e:
        print(f"\n--- Orchestration Failed ---", file=sys.stderr)
        print(f"Error: {e}", file=sys.stderr)
        # Logging is handled within the service methods
        sys.exit(1)

    print("\nScript finished.")
