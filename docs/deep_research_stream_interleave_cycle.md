------------

The supervisor is active on the main host thread 

if deep_research is true.

The supervisor will triage the users query, and 

then call its [scratchpad] --> [delegate_task] tools

------------



--------------------------------

The delegate_task function call is handled by 

DelegationMixin()

DelegationMixin.handle_delegate_research_task()

Spins up an ephemeral research worker and hands 

it the task from the supervisor

---------------------------------

------------------------------------------

The research worker has the following tools at hand:

WORKER_TOOLS = [
    read_web_page,
    scroll_web_page,
    perform_web_search,
    search_web_page,
    append_scratchpad,
]
-----------------------------------------



--------------------------------

DelegationMixin.handle_delegate_research_task()

spawns its own prompt life cycle, using the projectdavid sdk client

The research worker will use its tools to cycle through the research task 

During this time, extraneous chunks text during 'noisy', function calls

may be emitted by the research worker, usually describing what it is doing.

This is actually useful for UX purposes. These chunks are interleaved, and 

arrive at the users client.

---------------------------------



After the Resrearch worker is satisfied that it has enough information 

to synthesise its response, two things happen:

1. It streams its response as real time output for the user 

2. Its response is gathered and injected into the host thread as the tool response to 

the supervisors call. 

The Supervisor can then be disengaged from the host thread, and the user can 

have a seamless conversation about the research with the main assistant.

There is a potential bug issue because although the main assistant 

responds seamlessly under test conditions, it is actually not aware of 

the Supervisors tools, which the tool on its dialogue is responding to. 

Will address this if it becomes a future issue. 