````
USER: "Weather in London & Flights JFK->LHR"
____________________________________________________________________________________
                               TURN 1: THE OVER-AMBITIOUS PLAN
____________________________________________________________________________________
   [LLM MIND]        [ORCHESTRATOR PARSER]            [EXECUTION LAYER]
   |                 |                                |
   |-- <plan> -------|                                |
   |   "I will call  |                                |
   |    Weather AND  |                                |
   |    Flights."    |                                |
   |                 |                                |
   |-- <fc>Weather --|--> [Matches FIRST tag]         |
   |                 |    State = get_weather         |
   |                 |                                |
   |-- <fc>Flights --|--> [IGNORED BY PARSER]         |
   |                 |    (Parser stopped at 1st tag) |
   |_________________|________________________________|_____________________________
                                     |
                                     v
                          [SDK] Executes Weather
                          [SDK] Submits Result
                          [SDK] Triggers Turn 2 Recursion (Level 2 Logic)
____________________________________________________________________________________
                               TURN 2: THE "STILL NEED TO FINISH"
____________________________________________________________________________________
   [LLM MIND]        [ORCHESTRATOR PARSER]            [EXECUTION LAYER]
   |                 |                                |
   | (Sees Weather   |                                |
   |  in history)    |                                |
   |                 |                                |
   |-- <think> ------|                                |
   |   "I still need |                                |
   |    the flights."|                                |
   |                 |                                |
   |-- <fc>Flights --|--> [Matches tag]               |
   |                 |    State = get_flight_times    |
   |_________________|________________________________|_____________________________
                                     |
                                     v
                          [SDK] Executes Flight Times
                          [SDK] Submits Result
                          [SDK] Triggers Turn 3 Recursion
____________________________________________________________________________________
                               TURN 3: THE FINAL SYNTHESIS
____________________________________________________________________________________
   [LLM MIND]        [ORCHESTRATOR PARSER]            [EXECUTION LAYER]
   |                 |                                |
   | (Sees both results)                              |
   |-- "Here is the weather and your flights..."      |
   |__________________________________________________|_____________________________