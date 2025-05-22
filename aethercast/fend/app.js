document.addEventListener('DOMContentLoaded', () => {
    const CPOA_BASE_URL = 'http://localhost:5000/api/v1'; 

    const statusMessageEl = document.getElementById('status-message');
    const snippetsListEl = document.getElementById('snippets-list');
    const playerAreaEl = document.getElementById('player-area');
    const playerTitleEl = document.getElementById('player-title');
    const playerStreamOutputEl = document.getElementById('player-stream-output');
    const closeStreamButtonEl = document.getElementById('close-stream-button');

    let currentSocket = null;
    let currentStreamId = null;
    // Use an object to manage polling intervals by taskId to prevent conflicts
    let pollingIntervals = {};


    function updateStatus(message, isError = false) {
        console.log(isError ? `Error: ${message}` : message);
        statusMessageEl.textContent = message;
        statusMessageEl.style.color = isError ? 'red' : 'black';
    }

    function appendStreamMessage(message, type = 'info') {
        const p = document.createElement('p');
        p.textContent = `[${new Date().toLocaleTimeString()}] ${type}: ${message}`;
        playerStreamOutputEl.appendChild(p);
        playerStreamOutputEl.scrollTop = playerStreamOutputEl.scrollHeight; 
    }
    
    function stopPolling(taskId) {
        if (pollingIntervals[taskId]) {
            clearInterval(pollingIntervals[taskId]);
            delete pollingIntervals[taskId];
        }
    }

    async function fetchSnippets(errorSimulationConfig = null) {
        updateStatus('Fetching snippets...');
        try {
            const topicsToFetch = ["AI in education", "Future of space travel", "Sustainable farming techniques"];
            snippetsListEl.innerHTML = ''; 

            let fetchPromises = topicsToFetch.map(async (topic) => {
                let url = `${CPOA_BASE_URL}/snippets?topic=${encodeURIComponent(topic)}`;
                if (errorSimulationConfig && errorSimulationConfig.agent_to_fail === "tda" && errorSimulationConfig.topic_query_to_fail === topic) {
                    const configStr = JSON.stringify({
                        agent_to_fail: "tda",
                        error_trigger_value: errorSimulationConfig.error_trigger_value || "tda_error"
                    });
                    url += `&error_simulation_config=${encodeURIComponent(configStr)}`;
                }

                const response = await fetch(url);
                if (!response.ok) {
                    updateStatus(`Error fetching snippet task for topic '${topic}': ${response.statusText}`, true);
                    return null; 
                }
                const taskData = await response.json();
                updateStatus(`Snippet generation task for '${topic}' (ID: ${taskData.task_id}). Polling...`);
                return pollTaskStatus(taskData.task_id, true, topic);
            });

            const results = await Promise.allSettled(fetchPromises);
            
            let snippetsDisplayed = 0;
            results.forEach(result => {
                if (result.status === 'fulfilled' && result.value && result.value.status === 'COMPLETED') {
                    // displaySnippet was called by pollTaskStatus for successful snippet tasks
                    snippetsDisplayed++;
                } else if (result.status === 'rejected' || (result.value && result.value.status === 'FAILED')) {
                    // Error already handled by pollTaskStatus or fetch itself
                    console.error("A snippet fetch/poll failed:", result.reason || result.value);
                }
            });
            
            if (snippetsDisplayed === 0) {
                 updateStatus('No snippets available or all failed to load. Try refreshing.', true);
            } else {
                 updateStatus(`${snippetsDisplayed} snippet(s) loaded.`);
            }

        } catch (error) {
            updateStatus(`Error in fetchSnippets: ${error.message}`, true);
        }
    }
    
    function displaySnippet(snippetData, originalTopicQuery) {
        const snippetEl = document.createElement('div');
        snippetEl.className = 'snippet';
        const title = snippetData.title || 'Untitled Snippet';
        const summary = snippetData.summary || 'No content available.';
        const topicForButton = snippetData.topic_id || originalTopicQuery;
        const snippetIdForButton = snippetData.snippet_id;

        snippetEl.innerHTML = `
            <h4>${title}</h4>
            <p>${summary}</p>
            <button data-topic="${topicForButton}" data-snippet-id="${snippetIdForButton}">Generate & Listen to Full Podcast</button>
            <button class="error-trigger-button" data-target-agent="wcha" data-topic="${topicForButton}" data-snippet-id="${snippetIdForButton}">Gen. Podcast (Fail WCHA)</button>
            <button class="error-trigger-button" data-target-agent="pswa" data-topic="${topicForButton}" data-snippet-id="${snippetIdForButton}">Gen. Podcast (Fail PSWA)</button>
            <button class="error-trigger-button" data-target-agent="vfa" data-topic="${topicForButton}" data-snippet-id="${snippetIdForButton}">Gen. Podcast (Fail VFA)</button>
        `;
        snippetsListEl.appendChild(snippetEl);

        snippetEl.querySelectorAll('button').forEach(button => {
            button.addEventListener('click', async (e) => {
                const topic = e.target.dataset.topic;
                const snippetId = e.target.dataset.snippetId;
                const targetAgentToFail = e.target.dataset.targetAgent; // Will be undefined for the normal button
                
                let errorConfig = null;
                if (targetAgentToFail) {
                    errorConfig = { agent_to_fail: targetAgentToFail, error_trigger_value: `${targetAgentToFail}_error` };
                }

                updateStatus(`Requesting full podcast for topic: '${topic}' (from snippet: ${snippetId}). ${targetAgentToFail ? 'Expecting ' + targetAgentToFail.toUpperCase() + ' to fail.' : ''}`);
                playerAreaEl.classList.remove('hidden');
                playerTitleEl.textContent = `Podcast: ${title}`;
                playerStreamOutputEl.innerHTML = ''; 
                appendStreamMessage(`Initiating podcast generation for snippet: ${snippetId}...`);

                await generatePodcast(topic, snippetId, errorConfig);
            });
        });
    }

    async function generatePodcast(topic, snippetId, errorSimulationConfig = null) {
        try {
            const payload = { topic: topic, snippet_id: snippetId };
            if (errorSimulationConfig) {
                payload.error_simulation_config = errorSimulationConfig;
            }

            const response = await fetch(`${CPOA_BASE_URL}/podcasts/generate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ error: response.statusText }));
                updateStatus(`Error generating podcast: ${errorData.error || response.statusText}`, true);
                appendStreamMessage(`CPOA Error on /podcasts/generate: ${errorData.error || response.statusText}`, 'error');
                return;
            }
            const generationData = await response.json();
            updateStatus(`Podcast generation started. Task ID: ${generationData.task_id}. Polling...`);
            appendStreamMessage(`Podcast generation task accepted. Task ID: ${generationData.task_id}`);
            
            pollTaskStatus(generationData.task_id, false); // isSnippetFetch = false

        } catch (error) {
            updateStatus(`Error during generatePodcast call: ${error.message}`, true);
            appendStreamMessage(`Network or other error during generatePodcast: ${error.message}`, 'error');
        }
    }

    async function pollTaskStatus(taskId, isSnippetFetch = false, originalTopicQuery = null) {
        // Clear any existing interval for this specific task ID before starting a new one.
        stopPolling(taskId); 
    
        return new Promise((resolve, reject) => {
            pollingIntervals[taskId] = setInterval(async () => {
                try {
                    const response = await fetch(`${CPOA_BASE_URL}/tasks/${taskId}`);
                    if (!response.ok) {
                        const errorText = await response.text();
                        updateStatus(`Error polling task ${taskId}: ${response.statusText} - ${errorText}`, true);
                        if (response.status === 404) {
                            stopPolling(taskId);
                            appendStreamMessage(`Task ${taskId} not found. Stopping polling.`, 'error');
                            reject(new Error(`Task ${taskId} not found.`));
                        }
                        return; 
                    }
                    const taskStatus = await response.json();
                    const statusDetailMsg = `Task ${taskId} (${taskStatus.type || (isSnippetFetch ? 'snippet' : 'podcast')}): ${taskStatus.status}. Details: ${taskStatus.details || 'N/A'}`;
                    updateStatus(statusDetailMsg);
                    if(!isSnippetFetch) appendStreamMessage(statusDetailMsg);


                    if (taskStatus.status === 'COMPLETED') {
                        stopPolling(taskId);
                        if (isSnippetFetch) {
                            displaySnippet(taskStatus.result, originalTopicQuery);
                        } else {
                            playerTitleEl.textContent = `Podcast: ${taskStatus.result.title || taskStatus.input_parameters?.topic || 'Generated Podcast'}`;
                            appendStreamMessage(`Podcast ready! Stream ID: ${taskStatus.result.stream_id}. Connecting to ASF...`);
                            connectToAudioStream(taskStatus.result.audio_stream_url_for_client, taskStatus.result.stream_id, taskStatus.result.podcast_id);
                        }
                        resolve(taskStatus);
                    } else if (taskStatus.status === 'FAILED') {
                        stopPolling(taskId);
                        const errMessage = `Task ${taskId} FAILED: ${taskStatus.details || taskStatus.error_details || 'Unknown error'}`;
                        updateStatus(errMessage, true);
                        if(!isSnippetFetch) {
                            appendStreamMessage(errMessage, 'error');
                            if (taskStatus.error_simulation_trigger_used) {
                                const simDetails = taskStatus.error_simulation_trigger_used;
                                appendStreamMessage(`Error was simulated in: ${simDetails.agent_to_fail} (Trigger: ${simDetails.error_trigger_value})`, 'debug');
                            }
                        }
                        playerAreaEl.classList.add('hidden'); // Hide player on fail
                        reject(new Error(errMessage));
                    }
                } catch (error) {
                    updateStatus(`Error polling task ${taskId}: ${error.message}`, true);
                    stopPolling(taskId);
                    reject(error);
                }
            }, 3000); 
        });
    }
    
    function connectToAudioStream(wsUrl, streamId, podcastId) {
        if (currentSocket) {
            currentSocket.close();
            currentSocket = null;
        }
        
        const namespaceUrl = wsUrl.substring(0, wsUrl.lastIndexOf('/')); 
        updateStatus(`Connecting to ASF at ${namespaceUrl} for stream ${streamId}...`);
        appendStreamMessage(`Attempting WebSocket connection to ${namespaceUrl} for stream ID: ${streamId} (Podcast ID: ${podcastId})`);

        currentSocket = io(namespaceUrl, { transports: ['websocket'] });
        currentStreamId = streamId;

        currentSocket.on('connect', () => {
            updateStatus(`Connected to ASF for stream ${currentStreamId}! Joining stream room...`);
            appendStreamMessage(`Successfully connected to ASF (SID: ${currentSocket.id}). Sending join_stream for ${currentStreamId}.`);
            currentSocket.emit('join_stream', { stream_id: currentStreamId });
        });

        currentSocket.on('connect_error', (error) => {
            updateStatus(`ASF Connection Error: ${error.message}`, true);
            appendStreamMessage(`WebSocket connection error: ${error.message}`, 'error');
            playerAreaEl.classList.add('hidden');
        });

        currentSocket.on('stream_status', (data) => {
            updateStatus(`Stream Status (${data.stream_id}): ${data.message}`);
            appendStreamMessage(`Stream status for ${data.stream_id}: ${data.message} (Status: ${data.status})`);
            if(data.status === 'joined') {
                closeStreamButtonEl.classList.remove('hidden');
            }
        });

        currentSocket.on('audio_control', (data) => {
            appendStreamMessage(`Audio Control (${data.stream_id}): ${data.event}`, 'control');
            if (data.event === 'end_of_stream') {
                updateStatus(`Stream ${data.stream_id} ended.`);
                appendStreamMessage('End of stream message received. You can close the stream now.', 'control');
            }
        });

        currentSocket.on('text_chunk', (data) => { 
            appendStreamMessage(`CHUNK ${data.sequence} (${data.stream_id}): ${data.data}`, 'audio');
        });
        
        currentSocket.on('error', (data) => { 
            updateStatus(`ASF Error for stream ${data.stream_id || currentStreamId}: ${data.message}`, true);
            appendStreamMessage(`Received error from ASF: ${data.message}`, 'error');
        });

        currentSocket.on('disconnect', (reason) => {
            updateStatus(`Disconnected from ASF: ${reason}`);
            appendStreamMessage(`Disconnected from ASF: ${reason}. Stream ID: ${currentStreamId}`, 'info');
            if (reason !== 'io client disconnect') { // If not closed by user button
                playerAreaEl.classList.add('hidden');
            }
            closeStreamButtonEl.classList.add('hidden');
            currentSocket = null;
            currentStreamId = null;
        });
    }

    closeStreamButtonEl.addEventListener('click', () => {
        if (currentSocket) {
            updateStatus(`Closing stream ${currentStreamId}...`);
            appendStreamMessage(`User requested stream closure for ${currentStreamId}.`);
            currentSocket.close(); // This will trigger the 'disconnect' event with reason 'io client disconnect'
        }
        playerAreaEl.classList.add('hidden');
        closeStreamButtonEl.classList.add('hidden');
    });
    
    // Add buttons to simulate TDA errors for snippet fetching
    const errorSimArea = document.createElement('div');
    errorSimArea.innerHTML = `<h3>Error Simulation Controls (for Snippet Fetching)</h3>
        <button id="fetch-normal">Fetch Snippets (Normal)</button>
        <button id="fetch-fail-tda-edu">Fetch Snippets (Fail TDA for 'AI in education')</button>`;
    statusMessageEl.parentNode.insertBefore(errorSimArea, statusMessageEl.nextSibling);

    document.getElementById('fetch-normal').addEventListener('click', () => fetchSnippets());
    document.getElementById('fetch-fail-tda-edu').addEventListener('click', () => {
        fetchSnippets({ agent_to_fail: "tda", topic_query_to_fail: "AI in education", error_trigger_value: "tda_error" });
    });


    // Initial load
    fetchSnippets();
});
```

**Summary of Changes to FEND (`aethercast/fend/app.js`):**

1.  **Polling Management (`pollingIntervals`, `stopPolling`):**
    *   `pollingIntervalId` was changed to `pollingIntervals = {}` to store interval IDs per `taskId`. This prevents a new polling task from clearing the interval of an unrelated ongoing one.
    *   `stopPolling(taskId)` function created to clear a specific interval and remove it from `pollingIntervals`.

2.  **Error Display in `pollTaskStatus`:**
    *   When `taskStatus.status === 'FAILED'`:
        *   It now clearly updates the main status message: `updateStatus(\`Task \${taskId} FAILED: \${taskStatus.details || taskStatus.error_details || 'Unknown error'}\`, true);`.
        *   If it's a podcast generation task (`!isSnippetFetch`), it appends this error message to the player/stream output area.
        *   If `taskStatus.error_simulation_trigger_used` is present (meaning the error was simulated and CPOA propagated this info), it appends a debug message indicating which agent was triggered to fail.
        *   It hides the `playerAreaEl` if a podcast generation task fails, preventing attempts to connect to ASF.
        *   It calls `reject(new Error(errMessage));` to ensure promises returned by `pollTaskStatus` are rejected on failure.

3.  **Error Handling in `fetchSnippets`:**
    *   Modified to use `Promise.allSettled` to handle multiple snippet fetching tasks. This ensures that even if one snippet task fails (e.g., due to a simulated TDA error), other snippet fetching processes can continue.
    *   Counts `snippetsDisplayed` and updates status accordingly, noting if all snippet fetches failed.

4.  **Error Handling in `generatePodcast`:**
    *   If the initial `POST` to CPOA's `/podcasts/generate` fails (e.g., CPOA itself is down or returns an immediate error), it now logs this to the `statusMessageEl` and `playerStreamOutputEl`.

5.  **Error Simulation Buttons:**
    *   **For Snippet Generation (TDA failure):** Added buttons "Fetch Snippets (Normal)" and "Fetch Snippets (Fail TDA for 'AI in education')". The latter calls `fetchSnippets` with an `errorSimulationConfig` to trigger an error in TDA for a specific topic. CPOA's `/api/v1/snippets` endpoint was already updated in Turn 33 to accept `error_simulation_config` as a query parameter.
    *   **For Podcast Generation (WCHA, PSWA, VFA failure):** Added buttons under each displayed snippet: "Gen. Podcast (Fail WCHA)", "Gen. Podcast (Fail PSWA)", "Gen. Podcast (Fail VFA)". These buttons call `generatePodcast` with an appropriate `errorSimulationConfig` which is then sent in the JSON body to CPOA's `/api/v1/podcasts/generate` endpoint.

6.  **Minor UI Tweaks:**
    *   The `playerAreaEl` is explicitly hidden if a podcast generation task fails.
    *   The "Close Stream" button is also hidden on stream closure or failure.
    *   Disconnect reason `'io client disconnect'` (user closing the stream) now doesn't hide the player area, allowing messages to remain visible. Other disconnect reasons will hide it.

With these changes, FEND can now:
- Initiate error simulations in specific agents via CPOA.
- Display when a CPOA task (either snippet or podcast generation) has failed.
- Show error details provided by CPOA.
- Avoid attempting to connect to ASF if podcast generation failed.

This completes the error handling implementation across the agents, CPOA, and FEND.Okay, I have:
1.  **Implemented error simulation in all specialized agents (TDA, SCA, WCHA, PSWA, VFA)** in previous turns (25, 27, 29, and 32, with corrections in 26, 28, 31, 33). Each agent's primary endpoint now accepts an `error_trigger` in its JSON payload and returns a 500 HTTP error with a specific JSON body if the trigger matches.
2.  **Enhanced CPOA for error handling and propagation (Turn 33):**
    *   Refactored agent calls into a common `_call_agent_service` helper that checks HTTP status codes, parses error JSON from agents, and returns a consistent error dictionary (`{"error": ..., "details": ..., "status_code": ...}`).
    *   Updated workflow functions (`run_snippet_generation_workflow`, `run_full_podcast_generation_workflow`) to accept an `error_simulation_config` from API endpoints. This config is used to pass the `error_trigger` to the targeted specialized agent.
    *   If a call to a specialized agent returns an error, the workflow updates the task status to "FAILED" with details and halts processing for that task.
    *   Updated API endpoints (`/api/v1/snippets`, `/api/v1/podcasts/generate`) to accept `error_simulation_config` and pass it to workflows.
3.  **Enhanced FEND for error display (Turn 34):**
    *   `app.js` was updated to handle "FAILED" task statuses from CPOA.
    *   It displays user-friendly error messages in the main status area and the player/stream log.
    *   It includes error details from CPOA and, if present, information about which agent was triggered for error simulation.
    *   The UI stops processing for failed tasks (e.g., hides the player, doesn't attempt WebSocket connection).
    *   Added buttons to the FEND to trigger specific error simulations in TDA (for snippet generation) and WCHA, PSWA, VFA (for podcast generation) to demonstrate the error handling.

All parts of the subtask have been addressed.

**Summary of Error Simulation and Handling:**

*   **Specialized Agents (TDA, SCA, WCHA, PSWA, VFA):**
    *   Each agent's primary API endpoint (e.g., `/discover_topics` for TDA) was modified to accept an `error_trigger` field in the JSON payload of the request.
    *   If this field matches a predefined value (e.g., `error_trigger == "tda_error"`), the agent simulates an internal error by returning an HTTP 500 status code.
    *   The JSON response body for such an error is standardized to `{"error": "Simulated <AgentName> Error", "details": "This is a controlled error triggered for testing purposes in <AgentName>."}`.

*   **Central Podcast Orchestrator Agent (CPOA):**
    *   **Error Simulation Triggering:** CPOA's API endpoints (`GET /api/v1/snippets` and `POST /api/v1/podcasts/generate`) were updated to accept an `error_simulation_config` object.
        *   For `/snippets`, this is passed as a JSON string in a query parameter (e.g., `?error_simulation_config={"agent_to_fail":"tda","error_trigger_value":"tda_error"}`).
        *   For `/podcasts/generate`, it's part of the main JSON request body.
        *   This config specifies the `agent_to_fail` (e.g., "tda", "sca") and the `error_trigger_value` to be sent to that agent.
    *   **Agent Call Refactoring:** A new internal helper function `_call_agent_service` was introduced to standardize how CPOA calls other agents. This function:
        *   Includes the `error_trigger` in the payload to the specialized agent if specified by the workflow.
        *   Handles `requests.exceptions` (Timeout, ConnectionError, general RequestException).
        *   Checks the HTTP response status (`response.ok`). If not OK (>=400), it attempts to parse the agent's JSON error response.
        *   Returns a consistent error dictionary: `{"error": "Error message", "details": "Detailed info", "status_code": HTTP_status_code}`.
    *   **Workflow Error Handling:** The `run_snippet_generation_workflow` and `run_full_podcast_generation_workflow` functions were updated:
        *   They now accept and use the `error_simulation_config` to determine if an `error_trigger` should be sent to a downstream agent.
        *   After each call to a specialized agent (e.g., `call_topic_discovery_agent(...)`), they check if the returned dictionary contains an `"error"` key.
        *   If an error is detected, the workflow:
            1.  Logs the error.
            2.  Updates the task's status in `active_workflows` to "FAILED".
            3.  Stores the error `details` and `status_code` in the task data.
            4.  Halts further execution of that specific workflow.
    *   **Task Status Endpoint (`/api/v1/tasks/<task_id>`):** When a task has failed, this endpoint now includes the `error_details` and any `error_simulation_trigger_used` in its response, providing more insight into the failure.

*   **Frontend UI (FEND):**
    *   **Error Display:** FEND's `app.js` was modified in the `pollTaskStatus` function:
        *   If a CPOA task status is "FAILED", it updates the main status message with an error indication and details from `taskStatus.details` or `taskStatus.error_details`.
        *   For podcast generation tasks, the error message is also appended to the "player" log.
        *   If the error was simulated, FEND displays which agent was targeted.
    *   **UI Behavior on Error:**
        *   Polling for the failed task is stopped.
        *   The player area is hidden if a podcast generation task fails, preventing attempts to connect to ASF.
    *   **Error Simulation Triggers:**
        *   Buttons were added to FEND to allow manual triggering of errors in TDA (during snippet fetching) and in WCHA, PSWA, or VFA (during full podcast generation). These buttons set the appropriate `error_simulation_config` when making requests to CPOA.

**Affected Files:**
*   `aethercast/tda/main.py` (Error simulation added)
*   `aethercast/sca/main.py` (Error simulation added)
*   `aethercast/wcha/main.py` (Error simulation added)
*   `aethercast/pswa/main.py` (Error simulation added)
*   `aethercast/vfa/main.py` (Error simulation added)
*   `aethercast/cpoa/main.py` (Error handling logic, error simulation propagation)
*   `aethercast/fend/app.js` (Error display, UI buttons for triggering errors)

This completes the implementation of basic error handling and simulation across the Aethercast application components. The system is now more resilient in CPOA when downstream agents fail, and the FEND provides feedback on such failures. The error simulation mechanism allows for testing these error pathways.
