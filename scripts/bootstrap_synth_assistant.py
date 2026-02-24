#!
import argparse
import sys

try:
    from projectdavid import Entity
    from projectdavid_common import ValidationInterface
    from projectdavid_common.constants.assistant_map import PLATFORM_ASSISTANT_ID_MAP

    from entities_api.orchestration.instructions.synthesis import assemble_instructions
    from entities_api.services.logging_service import LoggingUtility
except ImportError as e:
    print(f"Error importing required modules: {e}")
    print(
        "Please ensure 'projectdavid', 'projectdavid_common', and local 'entities_api' modules are accessible."
    )
    sys.exit(1)


class AssistantSetupService:
    def __init__(self, client: Entity):
        if not isinstance(client, Entity):
            raise TypeError(
                "AssistantSetupService requires an initialized projectdavid.Entity client."
            )
        self.client = client
        self.logging_utility = LoggingUtility()

    def setup_assistant(
        self, user_id: str, assistant_name: str, instructions: str
    ) -> dict:
        try:
            assistant = self.client.assistants.create_assistant(
                name=assistant_name,
                description="Vector search result synthesis assistant",
                model="default",
                instructions=instructions,
                assistant_id=PLATFORM_ASSISTANT_ID_MAP["synthian"],
            )
            self.logging_utility.info(
                f"Created new assistant: '{assistant.name}' (ID: {assistant.id})"
            )
            return assistant
        except Exception as e:
            self.logging_utility.error(
                f"Failed to create assistant '{assistant_name}': {e}", exc_info=True
            )
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Set up a simple assistant with instructions.",
    )
    parser.add_argument("--api-key", help="The API key for authenticating.")
    parser.add_argument("--user-id", help="The User ID context for this operation.")
    parser.add_argument(
        "--base_workers-url",
        default=None,
        help="Optional base_workers URL for the API endpoint.",
    )
    parser.add_argument(
        "--name", default="Synth", help="Name of the assistant (default: 'Q')."
    )

    args = parser.parse_args()

    api_key = args.api_key or input("Enter your API key: ")
    user_id = args.user_id or input("Enter your User ID: ")
    base_url = args.base_url
    assistant_name = args.name
    instructions = assemble_instructions()

    try:
        client_params = {"api_key": api_key}
        if base_url:
            client_params["base_url"] = base_url

        api_client = Entity(**client_params)
        service = AssistantSetupService(api_client)
        assistant = service.setup_assistant(user_id, assistant_name, instructions)

        print(f"\nAssistant Created Successfully:")
        print(f"Name: {assistant.name}")
        print(f"ID: {assistant.id}")
    except Exception as e:
        print(f"\nFailed to create assistant: {e}")
        sys.exit(1)
