from entities_api.platform_tools.handlers.computer.shell_command_interface import (
    run_shell_commands,
)

commands_list = ["telnet host.docker.internal 9001"]
result = run_shell_commands(
    commands_list,
    thread_id="your_explicit_test_thread_room",
    idle_timeout=3000,
    elevated=False,
)
print("\nCommand results:")
print(result)
