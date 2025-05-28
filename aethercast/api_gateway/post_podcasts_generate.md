# Endpoint: POST /api/v1/podcasts/generate

## Description
This endpoint initiates the synchronous generation of a full podcast based on a given topic. It orchestrates calls to various backend agents (WCHA, PSWA, VFA) and returns the final result, including audio details and an orchestration log.

## Request Body
- Type: `application/json`
- **Schema:**
  ```json
  {
    "topic": "string"
  }
  ```
  - `topic`: (Required) The topic for the podcast to be generated.

- **Example:**
  ```json
  {
    "topic": "The Future of Renewable Energy"
  }
  ```

## Success Response (200 OK)
Indicates that the podcast generation process was attempted. The `status` field within the response will detail whether it was fully "completed", "completed_with_warnings", or "failed".

- Type: `application/json`
- **Schema:**
  ```json
  {
    "topic": "string",
    "status": "string",
    "final_audio_details": {
      "status": "string",
      "message": "string",
      "audio_url": "string",
      "script_char_count": "integer",
      "engine_used": "string"
    },
    "orchestration_log": [
      {
        "timestamp": "string",
        "message": "string",
        "data": "object or string"
      }
    ],
    "message": "string",
    "error_message": "string"
  }
  ```
  - `topic`: The topic for which the podcast was generated.
  - `status`: The overall status of the podcast generation (e.g., "completed", "failed", "completed_with_warnings").
  - `final_audio_details`: An object containing details from the VoiceForgeAgent (VFA).
    - `status`: Status from VFA (e.g., "success", "skipped").
    - `message`: Message from VFA.
    - `audio_url`: URL to the mock audio file (or null if skipped/failed).
    - `script_char_count`: Character count of the script processed by VFA.
    - `engine_used`: Mock engine identifier from VFA.
  - `orchestration_log`: An array of log objects, where each object contains:
      - `timestamp`: ISO 8601 timestamp of the log entry.
      - `message`: Description of the orchestration step.
      - `data`: (Optional) Data associated with the log step, could be a JSON string or a simple string.
  - `message`: (Optional) An overall message summarizing the outcome (e.g., "Podcast processed successfully."). This field might be more relevant if the main `status` isn't "failed".
  - `error_message`: (Optional) If the status is "failed", this field will contain a specific error message from the failing agent.

- **Example (Successful Generation):**
  ```json
  {
    "topic": "The Future of Renewable Energy",
    "status": "completed",
    "final_audio_details": {
      "status": "success",
      "message": "Mock audio generated successfully from script.",
      "audio_url": "http://placeholder.aethercast.io/audio/mock_episode_abc123xyz.mp3",
      "script_char_count": 780,
      "engine_used": "mock_tts_engine_v1"
    },
    "orchestration_log": [
      {
        "timestamp": "2023-10-27T10:00:00.000Z",
        "message": "Orchestration started for topic: 'The Future of Renewable Energy'."
      },
      {
        "timestamp": "2023-10-27T10:00:01.000Z",
        "message": "Calling WCHA: harvest_content with topic 'The Future of Renewable Energy'."
      },
      {
        "timestamp": "2023-10-27T10:00:02.000Z",
        "message": "WCHA: harvest_content returned.",
        "data": "AI is transforming healthcare by improving diagnostic accuracy..."
      },
      {
        "timestamp": "2023-10-27T10:00:03.000Z",
        "message": "Calling PSWA: weave_script with content from WCHA and topic 'The Future of Renewable Energy'."
      },
      {
        "timestamp": "2023-10-27T10:00:04.000Z",
        "message": "PSWA: weave_script returned.",
        "data": "\n[TITLE] Exploring: The Future Of Renewable Energy\n[INTRO]...\n"
      },
      {
        "timestamp": "2023-10-27T10:00:05.000Z",
        "message": "Calling VFA: forge_voice with script from PSWA."
      },
      {
        "timestamp": "2023-10-27T10:00:06.000Z",
        "message": "VFA: forge_voice returned.",
        "data": {
          "status": "success",
          "message": "Mock audio generated successfully from script.",
          "audio_url": "http://placeholder.aethercast.io/audio/mock_episode_abc123xyz.mp3",
          "script_char_count": 780,
          "engine_used": "mock_tts_engine_v1"
        }
      },
      {
        "timestamp": "2023-10-27T10:00:07.000Z",
        "message": "Orchestration completed for topic: 'The Future of Renewable Energy'."
      }
    ],
    "message": "Podcast processed successfully."
  }
  ```

- **Example (Generation Skipped by VFA due to short script):**
  ```json
  {
    "topic": "Short Topic",
    "status": "completed_with_warnings",
    "final_audio_details": {
      "status": "skipped",
      "message": "Script too short (length 15 < 20 chars), mock audio generation skipped.",
      "audio_url": null,
      "script_char_count": 15,
      "engine_used": "mock_tts_engine_v1"
    },
    "orchestration_log": [
      {
        "timestamp": "2023-10-27T10:01:00.000Z",
        "message": "Orchestration started for topic: 'Short Topic'."
      },
      {
        "timestamp": "2023-10-27T10:01:01.000Z",
        "message": "WCHA: harvest_content returned.",
        "data": "Short content."
      },
      {
        "timestamp": "2023-10-27T10:01:02.000Z",
        "message": "PSWA: weave_script returned.",
        "data": "\n[TITLE] Exploring: Short Topic\n[INTRO]...\n"
      },
      {
        "timestamp": "2023-10-27T10:01:03.000Z",
        "message": "VFA: forge_voice returned.",
        "data": {
          "status": "skipped",
          "message": "Script too short (length 15 < 20 chars), mock audio generation skipped.",
          "audio_url": null,
          "script_char_count": 15,
          "engine_used": "mock_tts_engine_v1"
        }
      },
      {
        "timestamp": "2023-10-27T10:01:04.000Z",
        "message": "Orchestration completed_with_warnings for topic: 'Short Topic'."
      }
    ],
    "message": "Podcast processed, but audio generation was skipped for a short script."
  }
  ```

## Error Responses

- **400 Bad Request**
  - Description: The request body is invalid (e.g., missing `topic`, or `topic` is not a string).
  - Response Body:
    ```json
    {
      "error": "Bad Request",
      "message": "Topic is required in the request body and must be a string."
    }
    ```

- **500 Internal Server Error**
  - Description: An unexpected error occurred during the podcast generation orchestration on the backend (e.g., an agent failed critically). The `status` in the response body might be "failed", and `error_message` would provide more context.
  - Response Body (Example):
    ```json
    {
      "topic": "Problematic Topic",
      "status": "failed",
      "final_audio_details": null,
      "orchestration_log": [
        {
          "timestamp": "2023-10-27T10:02:00.000Z",
          "message": "Orchestration started for topic: 'Problematic Topic'."
        },
        {
          "timestamp": "2023-10-27T10:02:01.000Z",
          "message": "WCHA: Error during harvest_content: Some_WCHA_Error",
          "data": { "error_type": "Exception" }
        }
      ],
      "error_message": "WCHA failed: Some_WCHA_Error"
    }
    ```
    *(Note: For a 500 error, the API Gateway might alternatively return a more generic error message like `{"error": "Internal Server Error", "message": "An error occurred during podcast generation."}` if it cannot retrieve detailed CPOA output.)*
