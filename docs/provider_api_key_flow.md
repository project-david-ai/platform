<pre>
+-----------------------------+
|        User Script          |
|-----------------------------|
| sync_stream.setup(         |
|   ...,                      |
|   api_key='some_key' <──────┼─ START: Key provided here
| )                           |
|                             |
| sync_stream.stream_chunks(  |
|   ...,                      |
| )                           |
+-----------------------------+
             |
             | api_key='some_key' stored in instance
             v
+---------------------------------+
| SynchronousInferenceStream      |
| (`sync_stream`)                 |
|---------------------------------|
| Method: stream_chunks()         |
| - Accesses self.api_key         |
| - Calls internal async helper   |
|   `_stream_chunks_async()`      |
|   which calls...                |
|---------------------------------|
|   self.inference_client         |
|     .stream_inference_response( |
|       ...,                      |
|       api_key=self.api_key <────┼─ Key passed along
|     )                           |
+---------------------------------+
             |
             | api_key='some_key' passed as argument
             v
+---------------------------------+
| InferenceClient                 |
| (`client.inference`)           |
|---------------------------------|
| Method: stream_inference_response()|
| - Receives 'api_key' parameter  |
| - Creates `payload` dictionary  |
|   `{ ...,                       |
|     "api_key": api_key, <───────┼─ Key added to payload dict
|     ...}`                       |
| - Validates payload (incl. key) |
|   using `StreamRequest` model   |
| - Makes HTTP POST request       |
+---------------------------------+
             |
             | HTTP POST Request to /v1/completions
             | Body: JSON payload containing "api_key": "some_key"
             v
  =================== NETWORK ====================
             |
             v
+---------------------------------------------+
|           FastAPI Backend Service           |
|---------------------------------------------|
| Route: POST /v1/completions                 |
|---------------------------------------------|
| 1. Receives HTTP Request                    |
| 2. Parses JSON payload body                 |
| 3. Validates & populates Pydantic model:    |
|    `request: ValidationInterface.StreamRequest`|
|    -> request.api_key == 'some_key' <───────┼─ Key available in request object
+---------------------------------------------+
             |
             | request.api_key passed down the call chain:
             | completions() -> provider_instance.process_conversation()
             v
+-------------------------------------+     +---------------------------------------------+
| InferenceProviderSelector /         | --> | Provider Instance (e.g., HyperbolicV3Inf.)  |
| InferenceArbiter                    |     |---------------------------------------------|
|-------------------------------------|     | Method: process_conversation(..., api_key)  |
| - Selects/Creates provider instance |     | - Receives api_key='some_key'                |
|   (may init `hyperbolic_client`     |     | - Calls self.stream_response(..., api_key)  |
|    with ENV KEY by default)         |     +---------------------------------------------+
+-------------------------------------+                      |
                                                             | api_key='some_key' passed down
                                                             v
             +-----------------------------------------------------------------------+
             | Provider Instance Method: stream_response(..., api_key)              |
             |-----------------------------------------------------------------------|
             | - Receives api_key='some_key'                                        |
             | - Calls super().stream_hyperbolic(..., api_key='some_key') |
             +-----------------------------------------------------------------------+
                                         |
                                         | api_key='some_key' passed down
                                         v
   +-------------------------------------------------------------------------------------------------+
   | BaseInference Method: stream_hyperbolic(..., api_key='some_key')                       |
   |-------------------------------------------------------------------------------------------------|
   | 1. Receives api_key='some_key'                                                                 |
   | 2. Checks if api_key is provided:                                                             |
   |    IF api_key == 'some_key':                                                                   |
   |       - Create **NEW Temporary `OpenAI` Client** using 'some_key'                             |
   |       - Set `client_to_use = <temporary_client>`                                               |
   |    ELSE (api_key is None):                                                                     |
   |       - Use existing `self.hyperbolic_client` (initialized w/ ENV KEY)                         |
   |       - Set `client_to_use = self.hyperbolic_client`                                           |
   | 3. Make API call using selected client:                                                        |
   |    `response = client_to_use.chat.completions.create(...)`                                     |
   |    (NO api_key passed into `.create()`, key is baked into the client)                          |
   +-------------------------------------------------------------------------------------------------+
                                                         |
                                                         | API Call using the chosen client
                                                         v
                                              =================== NETWORK ====================
                                                         | Actual API call to provider backend
                                                         v
                                              +-----------------------------+
                                              | External AI Service         |
                                              | (e.g., Hyperbolic API)      |
                                              +-----------------------------+
</pre>
