'use strict';

document.addEventListener('DOMContentLoaded', () => {
    // DOM Element References
    const topicInput = document.getElementById('topic-input');
    const generateBtn = document.getElementById('generate-btn');
    const statusMessagesDiv = document.getElementById('status-messages');
    const podcastDisplayDiv = document.getElementById('podcast-display');
    const podcastTopicTitle = document.getElementById('podcast-topic-title');
    const audioPlayer = document.getElementById('audio-player'); // For direct file playback
    const generationDetailsLog = document.getElementById('generation-details-log');

    // New DOM Element References for Snippets
    const podcastSnippetsSection = document.getElementById('podcast-snippets-section');
    const snippetListContainer = document.getElementById('snippet-list-container');
    const snippetStatusMessage = document.getElementById('snippet-status-message');
    const refreshSnippetsBtn = document.getElementById('refresh-snippets-btn');

    // Topic Exploration elements
    const exploreKeywordsInput = document.getElementById('explore-keywords-input');
    const exploreKeywordsBtn = document.getElementById('explore-keywords-btn');
    const exploredTopicsContainer = document.getElementById('explored-topics-container');
    const exploredTopicsStatus = document.getElementById('explored-topics-status');

    // MSE specific elements (from index.html)
    const mseAudioPlayer = document.getElementById('audio-player-mse');
    const streamingStatusDiv = document.getElementById('streaming-status');
    const generationProgressDisplay = document.getElementById('generation-progress-display');
    const retryStreamBtn = document.getElementById('retry-stream-btn');

    // Diagnostics Modal Elements
    const diagnosticsModal = document.getElementById('diagnostics-modal');
    const diagnosticsModalCloseBtn = document.getElementById('diagnostics-modal-close-btn');
    const diagPodcastIdSpan = document.getElementById('diag-podcast-id');
    const diagTopicSpan = document.getElementById('diag-topic');
    const diagOverallStatusSpan = document.getElementById('diag-overall-status');
    const diagFinalErrorSpan = document.getElementById('diag-final-error');
    const diagOrchestrationLogContainer = document.getElementById('diag-orchestration-log-container');

    // State variables
    let currentSocket = null;
    let mediaSource = null;
    let sourceBuffer = null;
    let audioQueue = [];
    let isAppendingBuffer = false;
    // const ASF_NAMESPACE = '/api/v1/podcasts/stream'; // Audio streaming namespace, CPOA provides full URL
    const UI_UPDATES_NAMESPACE = '/ui_updates'; // Dedicated namespace for UI status updates from ASF
    let end_of_stream_received = false; // Tracks if server signaled end of stream for audio

    let currentStreamId = null; // For retrying audio stream
    let currentAsfWebsocketUrl = null; // For retrying audio stream (this is the full audio stream URL)
    let currentAsfBaseUrl = null; // For UI updates socket (e.g., ws://localhost:5006)
    let simulatedProgressInterval = null; // For simulated generation progress

    let currentUiClientId = null; // Unique ID for this client session for UI updates
    let uiUpdateSocket = null; // WebSocket for UI updates

    function generateOrGetClientId() {
        if (!currentUiClientId) {
            currentUiClientId = Date.now().toString(36) + Math.random().toString(36).substring(2);
            console.log("Generated UI Client ID:", currentUiClientId);
        }
        return currentUiClientId;
    }

    /**
     * Updates the status message display.
     * @param {string} message - The message to display.
     * @param {string} type - The type of message ('info', 'generating', 'success', 'error').
     */
    function updateStatus(message, type = 'info', targetDiv = statusMessagesDiv) {
        if(targetDiv){
            targetDiv.innerHTML = ''; // Clear current content
            const p = document.createElement('p');
            p.textContent = message;
            targetDiv.className = `status-messages status-${type}`; // Set class for styling
            targetDiv.appendChild(p);
            targetDiv.style.display = 'block';
        } else {
            console.warn("updateStatus: Target div not found for message:", message);
        }
    }

    function stopSimulatedGenerationProgress() {
        if (simulatedProgressInterval) {
            clearInterval(simulatedProgressInterval);
            simulatedProgressInterval = null;
        }
        if(generationProgressDisplay) generationProgressDisplay.style.display = 'none';
    }

    function startSimulatedGenerationProgress(topic) {
        stopSimulatedGenerationProgress(); // Clear any existing interval
        if (!generationProgressDisplay) {
            console.error("generationProgressDisplay element not found!");
            return;
        }
        generationProgressDisplay.style.display = 'block';
        const messages = [
            `Gathering latest information for '${topic}'...`,
            `Analyzing content and identifying key insights for '${topic}'...`,
            `Drafting initial script segments for '${topic}'...`,
            `Refining script with AI for narrative flow for '${topic}'...`,
            `Selecting voice and preparing audio synthesis for '${topic}'...`,
            `Generating high-quality audio for '${topic}'...`,
            `Performing final checks and assembling podcast for '${topic}'...`
        ];
        let messageIndex = 0;
        updateStatus(messages[messageIndex], 'generating', generationProgressDisplay);
        messageIndex++;

        simulatedProgressInterval = setInterval(() => {
            if (messageIndex < messages.length) {
                updateStatus(messages[messageIndex], 'generating', generationProgressDisplay);
                messageIndex++;
            } else {
                // Stay on the last message or a generic "Finalizing..."
                updateStatus(`Finalizing podcast for '${topic}'... please wait.`, 'generating', generationProgressDisplay);
                // Optionally stop interval here if you don't want it to repeat the last message
                // clearInterval(simulatedProgressInterval);
            }
        }, 3500); // Adjust timing as needed (e.g., 3.5 seconds per message)
    }

    function showRetryButton() {
        if(retryStreamBtn) retryStreamBtn.style.display = 'block';
    }

    function hideRetryButton() {
        if(retryStreamBtn) retryStreamBtn.style.display = 'none';
    }

    // Event Listener for Generate Button Click (Main button)
    generateBtn.addEventListener('click', () => {
        const topic = topicInput.value.trim();
        if (!topic) {
            updateStatus("Please enter a topic to generate a podcast.", "error", statusMessagesDiv);
            return;
        }
        // Use the main statusMessagesDiv for the final outcome of this specific generation request
        triggerPodcastGeneration(topic, 'status-messages');
    });


    // Function to display podcast generation status (can be called by snippet generation too)
    function displayPodcastGenerationOutcome(topic, result, targetStatusDivId) {
        const targetStatusDiv = document.getElementById(targetStatusDivId) || statusMessagesDiv;
        stopSimulatedGenerationProgress(); // Stop simulated progress

        const { ok, status, data } = result;

        podcastTopicTitle.textContent = topic; // Set title early
        podcastDisplayDiv.classList.remove('hidden'); // Show the main display area

        if (streamingStatusDiv) { // Clear and hide old streaming status
            streamingStatusDiv.textContent = '';
            streamingStatusDiv.style.display = 'none';
        }
        hideRetryButton();


        if (!ok) {
            const errorDetail = data.message || data.error || (status ? `Server error ${status}` : 'Unknown API error');
            updateStatus(`Failed to start podcast generation for '${topic}'. Error: ${errorDetail}`, 'error', targetStatusDiv);
            if (generationDetailsLog) generationDetailsLog.textContent = JSON.stringify(data, null, 2);
            console.error("Error generating podcast:", data);
            audioPlayer.classList.add('hidden'); // Hide direct player
            mseAudioPlayer.classList.add('hidden'); // Hide MSE player
        } else {
            updateStatus(`Podcast task for '${topic}' processed. Status: ${data.generation_status || status}.`,
                         (data.asf_websocket_url || data.audio_url) ? 'success' : 'info',
                         targetStatusDiv);

            if (generationDetailsLog) generationDetailsLog.textContent = JSON.stringify(data.details || data, null, 2);

            // Initialize UI Update WebSocket if we have ASF details and a client ID
            // The data.asf_websocket_url is the one for AUDIO streaming, we need to derive the base for UI updates.
            if (data.asf_websocket_url && currentUiClientId) {
                try {
                    const urlObj = new URL(data.asf_websocket_url);
                    currentAsfBaseUrl = `${urlObj.protocol}//${urlObj.host}`; // e.g., ws://localhost:5006
                    console.log("Derived ASF Base URL for UI updates:", currentAsfBaseUrl);
                    initUIUpdateSocket(currentAsfBaseUrl, currentUiClientId);
                } catch (e) {
                    console.error("Error parsing ASF WebSocket URL to get base for UI updates:", e);
                    updateStatus("Could not establish UI update channel (URL parse error).", "error", generationProgressDisplay); // Show in progress display
                }
            }


            // Audio Streaming Logic (MSE)
            if (data.asf_websocket_url && data.final_audio_details && data.final_audio_details.stream_id) {
                console.log("Audio Streaming: ASF WebSocket URL found, attempting to stream:", data.asf_websocket_url, "Stream ID:", data.final_audio_details.stream_id);
                audioPlayer.classList.add('hidden');
                audioPlayer.src = '';
                mseAudioPlayer.classList.remove('hidden');
                initWebSocketStreaming(data.asf_websocket_url, data.final_audio_details.stream_id); // This uses the full audio stream URL
                updateStatus(targetStatusDiv.textContent + ' Attempting real-time audio stream.', 'success', targetStatusDiv);
            }
            // Fallback to direct audio URL if audio is complete and no streaming info
            else if (data.audio_url && data.generation_status === "completed") {
                console.log("Direct audio_url found, using standard audio player:", data.audio_url);
                mseAudioPlayer.classList.add('hidden');
                mseAudioPlayer.src = '';
                audioPlayer.src = data.audio_url;
                audioPlayer.load();
                audioPlayer.classList.remove('hidden');
                updateStatus(targetStatusDiv.textContent + ' Playing directly via audio URL.', 'success', targetStatusDiv);
            } else { // No stream and no direct URL, or not completed
                audioPlayer.classList.add('hidden');
                mseAudioPlayer.classList.add('hidden');
                if (data.generation_status !== "completed" && !data.asf_websocket_url) {
                     updateStatus(targetStatusDiv.textContent + ` Further details: ${data.message || 'Awaiting completion or stream setup.'}`, 'info', targetStatusDiv);
                }
            }
        }
        console.log("Podcast generation response:", data);

        // Add View Diagnostics button if podcast_id is available
        // Ensure targetStatusDiv is the main one for this button.
        const mainStatusArea = document.getElementById(targetStatusDivId) || statusMessagesDiv;
        if (data.podcast_id && mainStatusArea) {
            // Remove existing diagnostics button if any
            const existingBtn = mainStatusArea.querySelector('.view-diagnostics-btn');
            if (existingBtn) existingBtn.remove();

            const diagBtn = document.createElement('button');
            diagBtn.textContent = "View Diagnostics";
            diagBtn.classList.add('view-diagnostics-btn');
            diagBtn.dataset.podcastId = data.podcast_id;
            diagBtn.style.marginTop = "10px"; // Add some spacing
            mainStatusArea.appendChild(diagBtn); // Append to the status area
        }
    }

    // WebSocket and MediaSource Extensions Logic
    function initWebSocketStreaming(wsBaseUrl, streamId) {
        cleanupMSE(); // Clean up any previous MSE state before starting new
        currentAsfWebsocketUrl = wsBaseUrl; // Store for retry
        currentStreamId = streamId;       // Store for retry
        end_of_stream_received = false;   // Reset flag

        console.log(`Attempting to connect to WebSocket at: ${wsBaseUrl} for stream ID: ${streamId}`);
        currentSocket = io(wsBaseUrl, { reconnectionAttempts: 3 });

        updateStreamingStatus(`Connecting to audio stream for ${streamId}...`, false);

        currentSocket.on('connect', () => {
            console.log('ASF WebSocket connected! SID:', currentSocket.id);
            updateStreamingStatus('Connected. Joining stream...', false);
            currentSocket.emit('join_stream', { stream_id: streamId });
        });

        mediaSource = new MediaSource();
        mseAudioPlayer.src = URL.createObjectURL(mediaSource);
        // Add error listener for MediaSource itself
        mediaSource.addEventListener('sourceerror', (ev) => {
            console.error('MediaSource error:', ev);
            updateStreamingStatus('A MediaSource error occurred.', true);
            cleanupMSE();
            showRetryButton();
        });
        mediaSource.addEventListener('sourceopen', () => handleMediaSourceOpen(streamId));

        currentSocket.on('disconnect', (reason) => {
            console.log('ASF WebSocket disconnected:', reason);
            updateStreamingStatus(`Stream disconnected: ${reason}. You may need to retry.`, true);
            // Don't cleanupMSE here if we want retry to work with existing MediaSource if possible
            // However, typically a disconnect means the MediaSource might also be unusable.
            if (reason !== 'io client disconnect') { // if not intentionally disconnected by client
                showRetryButton();
            }
        });
        currentSocket.on('error', (error) => { // General socket error
            console.error('ASF WebSocket error:', error);
            updateStreamingStatus(`Stream connection error: ${error.message || error}.`, true);
            cleanupMSE();
            showRetryButton();
        });
         currentSocket.on('connect_error', (error) => { // Specific connection error
            console.error('ASF WebSocket connection error:', error);
            updateStreamingStatus(`Failed to connect to stream: ${error.message || error}.`, true);
            cleanupMSE();
            showRetryButton();
        });
    }

    function handleMediaSourceOpen(streamId) {
        console.log('MediaSource opened. Stream ID:', streamId);
        updateStreamingStatus('MediaSource ready. Waiting for audio data...', false);
        try {
            sourceBuffer = mediaSource.addSourceBuffer('audio/mpeg');
            sourceBuffer.addEventListener('updateend', () => {
                isAppendingBuffer = false;
                appendNextChunk();
                checkBufferingStatus(); // Update status after append
            });
            sourceBuffer.addEventListener('error', (ev) => {
                console.error('SourceBuffer error:', ev, sourceBuffer.error);
                updateStreamingStatus(`Error with audio buffer: ${sourceBuffer.error ? sourceBuffer.error.message : 'Unknown SourceBuffer error'}.`, true);
                cleanupMSE(); // Critical error with buffer
                showRetryButton();
            });
        } catch (e) {
            console.error('Error adding SourceBuffer:', e);
            updateStreamingStatus(`Error setting up audio buffer: ${e.message}`, true);
            cleanupMSE();
            showRetryButton();
            return;
        }

        currentSocket.on('audio_chunk', (data) => {
            if (data instanceof ArrayBuffer) {
                audioQueue.push(data);
                appendNextChunk();
            } else {
                console.warn("Received audio_chunk that is not an ArrayBuffer:", data);
            }
        });

        currentSocket.on('audio_control', (message) => {
            console.log('Received audio_control:', message);
            if (message.event === 'start_of_stream') {
                updateStreamingStatus('Audio stream started...', false);
            } else if (message.event === 'end_of_stream') {
                end_of_stream_received = true;
                updateStreamingStatus('End of stream signal received. Finishing up...', false);
                checkIfStreamFinished(); // Check if we can end MediaSource
            }
        });

        currentSocket.on('stream_error', (error) => { // Error signaled by server on this specific stream
            console.error('Received stream_error from ASF:', error);
            updateStreamingStatus(`Error from stream: ${error.message}`, true);
            cleanupMSE();
            if(currentSocket) currentSocket.disconnect(); // Disconnect this socket
            showRetryButton();
        });
    }

    function checkIfStreamFinished() {
        if (end_of_stream_received && audioQueue.length === 0 && !isAppendingBuffer && sourceBuffer && !sourceBuffer.updating) {
            if (mediaSource.readyState === 'open') {
                try {
                    mediaSource.endOfStream();
                    console.log('MediaSource stream ended.');
                    updateStreamingStatus('Stream finished.', false);
                } catch (e) {
                    console.error("Error ending MediaSource stream:", e);
                    updateStreamingStatus(`Error finalizing stream: ${e.message}`, true);
                }
            }
        }
    }

    function appendNextChunk() {
        checkBufferingStatus(); // Update status before trying to append
        if (!isAppendingBuffer && audioQueue.length > 0 && sourceBuffer && !sourceBuffer.updating && mediaSource.readyState === 'open') {
            isAppendingBuffer = true;
            const chunk = audioQueue.shift();
            try {
                sourceBuffer.appendBuffer(chunk);
                // Status will be updated via 'updateend' calling checkBufferingStatus
            } catch (e) {
                console.error('Error appending buffer:', e);
                updateStreamingStatus(`Error buffering audio: ${e.message}.`, true);
                isAppendingBuffer = false;
                if (e.name === 'QuotaExceededError') {
                    audioQueue = [];
                    if (mediaSource.readyState === 'open') {
                        try { mediaSource.endOfStream(); } catch (e_eos) { console.error("Error ending stream on QuotaExceeded:", e_eos); }
                    }
                    showRetryButton(); // Quota issue might require a retry
                }
            }
        } else {
             checkIfStreamFinished(); // If not appending, check if stream is actually finished
        }
    }

    function checkBufferingStatus() {
        if (!sourceBuffer || mediaSource.readyState !== 'open') {
            // updateStreamingStatus("Stream not active.", false); // Or clear status
            return;
        }

        if (isAppendingBuffer || sourceBuffer.updating) {
            updateStreamingStatus(`Buffering audio... Queue: ${audioQueue.length}`, false);
        } else if (audioQueue.length > 0) {
            updateStreamingStatus(`Pending audio data in queue: ${audioQueue.length}. Waiting to append.`, false);
        } else if (!end_of_stream_received) {
            updateStreamingStatus('Buffer ready. Waiting for more audio data...', false);
        } else { // end_of_stream_received is true, queue is empty, not updating
            updateStreamingStatus('All audio data buffered. Stream finished.', false);
        }
    }

    function updateStreamingStatus(message, isError = false) {
        if (streamingStatusDiv) {
            streamingStatusDiv.textContent = message;
            streamingStatusDiv.className = `status-messages status-${isError ? 'error' : 'info'}`;
            streamingStatusDiv.style.display = message ? 'block' : 'none'; // Hide if message is empty
            console.log("Streaming Status:", message);
        }
    }

    function cleanupMSE() {
        console.log("Cleaning up MSE resources and UI update socket...");
        if (currentSocket) { // Audio stream socket
            currentSocket.disconnect();
            currentSocket = null;
        }
        if (uiUpdateSocket) { // UI update socket
            uiUpdateSocket.disconnect();
            uiUpdateSocket = null;
        }
        if (mediaSource && mediaSource.readyState === 'open') {
            try {
                if (sourceBuffer) {
                     if (!sourceBuffer.updating) {
                        mediaSource.removeSourceBuffer(sourceBuffer);
                     } else {
                        // Wait for update to end before removing, or force if necessary
                        console.warn("SourceBuffer is updating during cleanup. May cause issues.");
                        // Forcing removal might be okay if we are totally resetting
                        try { mediaSource.removeSourceBuffer(sourceBuffer); } catch(e_rb){ console.error("Forced removeSourceBuffer failed:", e_rb);}
                     }
                }
                 // Only call endOfStream if it hasn't been called or if it's safe
                if (end_of_stream_received && mediaSource.readyState === 'open' && (!sourceBuffer || !sourceBuffer.updating)) {
                    // mediaSource.endOfStream(); // This might be called too early or redundantly
                }
            } catch (e) {
                console.warn("Error during MediaSource resource cleanup:", e);
            }
        } else if (mediaSource) { // If not open, but exists
             console.log("MediaSource exists but not open. State:", mediaSource.readyState);
        }

        sourceBuffer = null;
        mediaSource = null; // Crucial to set this to null so a new one is created
        isAppendingBuffer = false;
        audioQueue = [];
        end_of_stream_received = false;

        if(mseAudioPlayer) {
            if (mseAudioPlayer.src) {
                URL.revokeObjectURL(mseAudioPlayer.src);
                mseAudioPlayer.removeAttribute('src');
            }
            mseAudioPlayer.load(); // Reset
            mseAudioPlayer.classList.add('hidden');
        }
        if(streamingStatusDiv) streamingStatusDiv.style.display = 'none';
        hideRetryButton();
        currentStreamId = null;
        currentAsfWebsocketUrl = null; // For audio stream
        currentAsfBaseUrl = null; // For UI updates
        // currentUiClientId is preserved for the session
    }

    async function triggerPodcastGeneration(topic, statusDivId) {
        const targetStatusDiv = document.getElementById(statusDivId) || statusMessagesDiv;
        generateOrGetClientId(); // Ensure we have a client ID for this session

        generateBtn.disabled = true;
        audioPlayer.classList.add('hidden');
        mseAudioPlayer.classList.add('hidden');
        podcastDisplayDiv.classList.add('hidden');
        if(streamingStatusDiv) streamingStatusDiv.style.display = 'none';
        hideRetryButton();

        cleanupMSE(); // Clean up any previous MSE state (includes UI socket)

        startSimulatedGenerationProgress(topic);

        try {
            const payload = {
                topic: topic,
                client_id: currentUiClientId // Include client_id in the request
            };
            // Add voice_params if you have them stored from UI settings
            // payload.voice_params = { ... };

            const response = await fetch('/api/v1/podcasts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const responseOk = response.ok;
            const data = await response.json();
            displayPodcastGenerationOutcome(topic, { ok: responseOk, status: response.status, data }, statusDivId);
        } catch (error) {
            console.error(`Error in triggerPodcastGeneration for topic "${topic}":`, error);
            stopSimulatedGenerationProgress();
            updateStatus(`Failed to start podcast generation for '${topic}'. Error: ${error.message || 'Network error or API unreachable.'}`, 'error', targetStatusDiv);
            if (uiUpdateSocket) uiUpdateSocket.disconnect(); // Disconnect UI socket on hard error here
        } finally {
            generateBtn.disabled = false;
        }
    }

    function initUIUpdateSocket(asfBaseUrlToParse, clientIdToSubscribe) {
        if (uiUpdateSocket) {
            uiUpdateSocket.disconnect();
            uiUpdateSocket = null;
        }

        let actualAsfBaseUrl;
        try {
            // ASF provides the full audio stream URL, e.g., ws://localhost:5006/api/v1/podcasts/stream
            // We need the base part, e.g., ws://localhost:5006
            const parsedUrl = new URL(asfBaseUrlToParse);
            actualAsfBaseUrl = `${parsedUrl.protocol}//${parsedUrl.host}`;
        } catch (e) {
            console.error("Failed to parse ASF base URL for UI updates:", e);
            updateStatus("Error setting up UI update connection (URL issue).", 'error', generationProgressDisplay);
            return;
        }

        console.log(`Attempting to connect to UI updates at: ${actualAsfBaseUrl}${UI_UPDATES_NAMESPACE} for client ID: ${clientIdToSubscribe}`);
        uiUpdateSocket = io(actualAsfBaseUrl + UI_UPDATES_NAMESPACE, { reconnectionAttempts: 3 });

        uiUpdateSocket.on('connect', () => {
            console.log('UI Update WebSocket connected. SID:', uiUpdateSocket.id);
            updateStatus('UI update channel connected. Subscribing...', 'info', generationProgressDisplay);
            uiUpdateSocket.emit('subscribe_to_ui_updates', { client_id: clientIdToSubscribe });
        });

        uiUpdateSocket.on('subscribed_ui_updates', (data) => {
            console.log('Subscribed to UI updates:', data);
            if (data.client_id === clientIdToSubscribe) {
                updateStatus('Successfully subscribed for real-time UI updates.', 'info', generationProgressDisplay);
            }
        });

        uiUpdateSocket.on('generation_status', (data) => {
            console.log('Received generation_status update:', data);
            stopSimulatedGenerationProgress(); // Stop simulated progress
            updateStatus(data.message, data.status_type || 'generating', generationProgressDisplay); // status_type could be 'info', 'generating', 'warning'
        });

        uiUpdateSocket.on('task_error', (data) => {
            console.error('Received task_error update:', data);
            stopSimulatedGenerationProgress();
            updateStatus(`Error during podcast generation: ${data.message}`, 'error', statusMessagesDiv); // Show final error in main status
            if(generationProgressDisplay) generationProgressDisplay.style.display = 'none';
            if (uiUpdateSocket) uiUpdateSocket.disconnect(); // Disconnect on terminal error
        });

        uiUpdateSocket.on('ui_error', (data) => {
            console.error('UI Update WebSocket error from server:', data);
            updateStatus(`UI update channel error: ${data.message}`, 'error', generationProgressDisplay);
        });

        uiUpdateSocket.on('disconnect', (reason) => {
            console.log('UI Update WebSocket disconnected:', reason);
            // Don't show error if it was a clean disconnect (e.g. server shutdown, or client initiated)
            if (reason !== 'io server disconnect' && reason !== 'io client disconnect') {
                 updateStatus(`UI update channel disconnected: ${reason}. Manual refresh might be needed if generation is ongoing.`, 'warning', generationProgressDisplay);
            }
        });
        uiUpdateSocket.on('connect_error', (error) => {
            console.error('UI Update WebSocket connection error:', error);
            updateStatus(`Failed to connect to UI update channel: ${error.message || error}. Progress may not be real-time.`, 'error', generationProgressDisplay);
        });
    }

    // Add listeners to MSE Audio Player for stalled/waiting events
    if (mseAudioPlayer) {
        mseAudioPlayer.addEventListener('stalled', () => {
            updateStreamingStatus('Stream stalled, possibly due to network. Trying to buffer...', false);
            // Optionally, you could try to nudge the stream or show a more prominent warning.
        });
        mseAudioPlayer.addEventListener('waiting', () => {
            updateStreamingStatus('Playback paused, waiting for more data to buffer...', false);
            checkBufferingStatus(); // Re-check buffer status
        });
         mseAudioPlayer.addEventListener('error', (e) => {
            console.error('MSE Audio Player Error:', e, mseAudioPlayer.error);
            updateStreamingStatus(`Audio playback error: ${mseAudioPlayer.error ? mseAudioPlayer.error.message : 'Unknown error'}. Try retrying the stream.`, true);
            cleanupMSE();
            showRetryButton();
        });
    }

    // Retry button event listener
    if(retryStreamBtn) {
        retryStreamBtn.addEventListener('click', () => {
            console.log("Retry Stream button clicked.");
            if (currentAsfWebsocketUrl && currentStreamId) {
                updateStreamingStatus(`Retrying stream for ID: ${currentStreamId}...`, false);
                cleanupMSE(); // Ensure clean state before retrying
                // Short delay to ensure cleanup completes
                setTimeout(() => {
                    initWebSocketStreaming(currentAsfWebsocketUrl, currentStreamId);
                }, 100);
            } else {
                updateStreamingStatus("Cannot retry: Stream details not available.", true);
            }
        });
    }


    async function fetchSnippets() {
        updateStatus('Loading fresh snippets...', 'info', snippetStatusMessage);
        snippetListContainer.innerHTML = ''; // Clear previous snippets
        podcastSnippetsSection.classList.remove('hidden'); // Ensure section is visible

        try {
            const response = await fetch('/api/v1/snippets');
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ message: `HTTP error ${response.status}` }));
                throw new Error(errorData.message || `Failed to fetch snippets. Status: ${response.status}`);
            }
            const data = await response.json();

            if (data.snippets && data.snippets.length > 0) {
                if(snippetStatusMessage) snippetStatusMessage.style.display = 'none'; // Hide status if snippets found
                data.snippets.forEach(snippet => {
                    const snippetCard = document.createElement('div');
                    snippetCard.className = 'snippet-card';

                    const titleEl = document.createElement('h3');
                    titleEl.textContent = snippet.title || (snippet.topic_info && snippet.topic_info.title_suggestion) || 'Untitled Snippet';

                    const summaryEl = document.createElement('p');
                    summaryEl.textContent = snippet.summary || snippet.text_content || 'No content available.';

                    const generateBtnLocal = document.createElement('button');
                    generateBtnLocal.textContent = 'Generate Podcast from this Snippet';
                    generateBtnLocal.classList.add('generate-podcast-snippet-btn');
                    const topicForGeneration = titleEl.textContent; // Use the displayed title as topic
                    generateBtnLocal.dataset.topic = topicForGeneration; // Store topic in data attribute

                    snippetCard.appendChild(titleEl);
                    snippetCard.appendChild(summaryEl);
                    snippetCard.appendChild(generateBtnLocal);
                    snippetListContainer.appendChild(snippetCard);
                });
            } else {
                updateStatus('No snippets available at the moment. Try refreshing!', 'info', snippetStatusMessage);
            }
        } catch (error) {
            console.error('Error fetching snippets:', error);
            updateStatus(`Error loading snippets: ${error.message}`, 'error', snippetStatusMessage);
        }
    }

    // Event listener for "Refresh Snippets" button
    if (refreshSnippetsBtn) {
        refreshSnippetsBtn.addEventListener('click', fetchSnippets);
    }

    // Event delegation for "Generate Podcast" buttons on snippets
    snippetListContainer.addEventListener('click', (event) => {
        if (event.target && event.target.classList.contains('generate-podcast-snippet-btn')) {
            const topic = event.target.dataset.topic;
            if (topic) {
                triggerPodcastGeneration(topic, 'status-messages'); // Use main status for snippet-triggered generation
                // Scroll to the main status/output area
                const mainStatusArea = document.getElementById('status-messages');
                if(mainStatusArea) mainStatusArea.scrollIntoView({ behavior: 'smooth' });
            } else {
                console.error("No topic found on snippet button.");
                 updateStatus("Error: Could not determine topic from snippet button.", 'error', statusMessagesDiv);
            }
        }
    });

    // Initial calls
    updateStatus("Enter a topic and click 'Generate Podcast', explore keywords, or choose a snippet below.", "info", statusMessagesDiv);
    fetchSnippets(); // Fetch snippets on page load
    cleanupMSE(); // Ensure clean MSE state on load

    // --- Topic Exploration Logic ---

    function renderSnippetCard(snippet, containerElement) {
        const snippetCard = document.createElement('div');
        snippetCard.className = 'snippet-card';

        const titleEl = document.createElement('h3');
        // Snippets from CPOA's orchestrate_snippet_generation have 'title'.
        // Snippets from DB (via /api/v1/snippets cache) might also use 'title'.
        // TDA's TopicObjects (if directly rendered, though less likely now) have 'title_suggestion'.
        titleEl.textContent = snippet.title || snippet.title_suggestion || 'Untitled Topic/Snippet';

        const summaryEl = document.createElement('p');
        // CPOA snippets have 'summary'. TDA TopicObjects also have 'summary'.
        summaryEl.textContent = snippet.summary || snippet.text_content || 'No content available.';

        const generateBtnLocal = document.createElement('button');
        generateBtnLocal.textContent = 'Generate Podcast';
        generateBtnLocal.classList.add('generate-podcast-snippet-btn');
        // Use snippet.title or snippet.title_suggestion as topic for generation
        const topicForGeneration = snippet.title || snippet.title_suggestion || "Selected Topic";
        generateBtnLocal.dataset.topic = topicForGeneration;

        const exploreBtnLocal = document.createElement('button');
        exploreBtnLocal.textContent = 'Explore Related';
        exploreBtnLocal.classList.add('explore-related-btn');
        // Use snippet.id if it's a topic ID, or snippet.topic_id if it's a snippet derived from a topic.
        // Assuming 'id' from topics_snippets table is the relevant unique ID for exploration.
        exploreBtnLocal.dataset.topicId = snippet.id || snippet.topic_id || (snippet.topic_info ? snippet.topic_info.topic_id : null);
        exploreBtnLocal.disabled = !(snippet.id || snippet.topic_id || (snippet.topic_info && snippet.topic_info.topic_id)); // Disable if no ID

        snippetCard.appendChild(titleEl);
        snippetCard.appendChild(summaryEl);
        snippetCard.appendChild(generateBtnLocal);
        snippetCard.appendChild(exploreBtnLocal);
        containerElement.appendChild(snippetCard);
    }


    async function triggerTopicExploration(payload) {
        if (!exploredTopicsStatus || !exploredTopicsContainer) {
            console.error("Exploration UI elements not found.");
            updateStatus("Exploration UI is not properly set up.", "error", statusMessagesDiv);
            return;
        }
        updateStatus("Exploring related topics...", "generating", exploredTopicsStatus);
        exploredTopicsContainer.innerHTML = ''; // Clear previous exploration results

        try {
            const response = await fetch('/api/v1/topics/explore', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ message: `HTTP error ${response.status}` }));
                throw new Error(errorData.message || `Topic exploration failed. Status: ${response.status}`);
            }
            const data = await response.json();

            if (data.explored_topics_or_snippets && data.explored_topics_or_snippets.length > 0) {
                updateStatus(`Found ${data.explored_topics_or_snippets.length} related items.`, "success", exploredTopicsStatus);
                data.explored_topics_or_snippets.forEach(item => {
                    renderSnippetCard(item, exploredTopicsContainer);
                });
            } else {
                updateStatus("No further related topics or snippets found for this exploration.", "info", exploredTopicsStatus);
            }

        } catch (error) {
            console.error('Error during topic exploration:', error);
            updateStatus(`Topic exploration error: ${error.message}`, "error", exploredTopicsStatus);
        }
    }

    function handleExploreRelated(event) {
        const topicId = event.target.dataset.topicId;
        if (topicId) {
            console.log("Exploring related to topic_id:", topicId);
            triggerTopicExploration({ current_topic_id: topicId, depth: "deeper" });
            if(exploredTopicsStatus) exploredTopicsStatus.scrollIntoView({ behavior: 'smooth' });
        } else {
            console.error("No topic_id found on explore button.");
            updateStatus("Cannot explore: topic ID missing.", "error", exploredTopicsStatus);
        }
    }

    // Event listener for keyword-based exploration
    if (exploreKeywordsBtn) {
        exploreKeywordsBtn.addEventListener('click', () => {
            const keywordsStr = exploreKeywordsInput.value.trim();
            if (keywordsStr) {
                const keywordsArray = keywordsStr.split(',').map(kw => kw.trim()).filter(kw => kw.length > 0);
                if (keywordsArray.length > 0) {
                    console.log("Exploring keywords:", keywordsArray);
                    triggerTopicExploration({ keywords: keywordsArray, depth: "deeper" });
                     if(exploredTopicsStatus) exploredTopicsStatus.scrollIntoView({ behavior: 'smooth' });
                } else {
                    updateStatus("Please enter valid keywords to explore.", "error", exploredTopicsStatus);
                }
            } else {
                updateStatus("Please enter keywords to explore.", "error", exploredTopicsStatus);
            }
        });
    }

    // Update fetchSnippets to use renderSnippetCard
    // And attach explore related listeners to snippetListContainer
    // This replaces the original fetchSnippets content
    async function newFetchSnippets() {
        updateStatus('Loading fresh snippets...', 'info', snippetStatusMessage);
        snippetListContainer.innerHTML = '';
        podcastSnippetsSection.classList.remove('hidden');

        try {
            const response = await fetch('/api/v1/snippets');
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ message: `HTTP error ${response.status}` }));
                throw new Error(errorData.message || `Failed to fetch snippets. Status: ${response.status}`);
            }
            const data = await response.json();

            if (data.snippets && data.snippets.length > 0) {
                if(snippetStatusMessage) snippetStatusMessage.style.display = 'none';
                data.snippets.forEach(snippet => {
                    renderSnippetCard(snippet, snippetListContainer); // Use the new rendering function
                });
            } else {
                updateStatus('No snippets available at the moment. Try refreshing!', 'info', snippetStatusMessage);
            }
        } catch (error) {
            console.error('Error fetching snippets:', error);
            updateStatus(`Error loading snippets: ${error.message}`, 'error', snippetStatusMessage);
        }
    }
    // Replace original fetchSnippets with newFetchSnippets
    // This requires removing or commenting out the old fetchSnippets if it exists below this block.
    // For this tool, I'll assume the new function replaces the old one if named identically,
    // or I call newFetchSnippets instead of fetchSnippets. Let's rename for clarity.
    const fetchAndRenderSnippets = newFetchSnippets;


    // Event listener for "Refresh Snippets" button
    if (refreshSnippetsBtn) {
        refreshSnippetsBtn.addEventListener('click', fetchAndRenderSnippets);
    }

    // Event delegation for "Generate Podcast" and "Explore Related" buttons on snippets
    snippetListContainer.addEventListener('click', (event) => {
        if (event.target) {
            if (event.target.classList.contains('generate-podcast-snippet-btn')) {
                const topic = event.target.dataset.topic;
                if (topic) {
                    triggerPodcastGeneration(topic, 'status-messages');
                    const mainStatusArea = document.getElementById('status-messages');
                    if(mainStatusArea) mainStatusArea.scrollIntoView({ behavior: 'smooth' });
                } else {
                    console.error("No topic found on snippet button.");
                    updateStatus("Error: Could not determine topic from snippet button.", 'error', statusMessagesDiv);
                }
            } else if (event.target.classList.contains('explore-related-btn')) {
                handleExploreRelated(event);
            }
        }
    });

    // Also delegate for explored topics container
    if (exploredTopicsContainer) {
        exploredTopicsContainer.addEventListener('click', (event) => {
            if (event.target) {
                if (event.target.classList.contains('generate-podcast-snippet-btn')) {
                    const topic = event.target.dataset.topic;
                    if (topic) {
                        triggerPodcastGeneration(topic, 'status-messages');
                        const mainStatusArea = document.getElementById('status-messages');
                        if(mainStatusArea) mainStatusArea.scrollIntoView({ behavior: 'smooth' });
                    } else {
                        console.error("No topic found on explored snippet button.");
                        updateStatus("Error: Could not determine topic from explored snippet button.", 'error', statusMessagesDiv);
                    }
                } else if (event.target.classList.contains('explore-related-btn')) {
                    handleExploreRelated(event);
                }
            }
        });
    }

    // Initial calls
    updateStatus("Enter a topic and click 'Generate Podcast', explore keywords, or choose a snippet below.", "info", statusMessagesDiv);
    fetchAndRenderSnippets(); // Fetch snippets on page load using the new function
    cleanupMSE(); // Ensure clean MSE state on load

    // --- Diagnostics Modal Logic ---
    function escapeHtml(unsafe) {
        if (unsafe === null || typeof unsafe === 'undefined') return '';
        return unsafe.toString()
             .replace(/&/g, "&amp;")
             .replace(/</g, "&lt;")
             .replace(/>/g, "&gt;")
             .replace(/"/g, "&quot;")
             .replace(/'/g, "&#039;");
    }

    async function handleViewDiagnostics(event) {
        const podcastId = event.target.dataset.podcastId;
        if (!podcastId || !diagnosticsModal) return;

        // Clear previous content & show loading
        diagPodcastIdSpan.textContent = podcastId;
        diagTopicSpan.textContent = 'Loading...';
        diagOverallStatusSpan.textContent = 'Loading...';
        diagFinalErrorSpan.textContent = 'Loading...';
        diagOrchestrationLogContainer.innerHTML = '<p>Loading diagnostics...</p>';
        diagnosticsModal.style.display = 'block';

        try {
            const response = await fetch(`/api/v1/podcasts/${podcastId}`);
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ message: `HTTP error ${response.status}` }));
                throw new Error(errorData.message || `Failed to fetch diagnostics. Status: ${response.status}`);
            }
            const data = await response.json();

            diagTopicSpan.textContent = escapeHtml(data.topic);
            diagOverallStatusSpan.textContent = escapeHtml(data.status);
            diagFinalErrorSpan.textContent = data.error_message ? escapeHtml(data.error_message) : 'None';

            diagOrchestrationLogContainer.innerHTML = ''; // Clear "Loading..."
            if (data.orchestration_log && Array.isArray(data.orchestration_log)) {
                if (data.orchestration_log.length === 0) {
                    diagOrchestrationLogContainer.innerHTML = '<p>No orchestration log entries found.</p>';
                } else {
                    data.orchestration_log.forEach(entry => {
                        const logEntryDiv = document.createElement('div');
                        logEntryDiv.classList.add('log-entry');

                        let content = `<p><strong>Timestamp:</strong> ${escapeHtml(entry.timestamp)}</p>`;
                        if (entry.stage) content += `<p><strong>Stage:</strong> ${escapeHtml(entry.stage)}</p>`;
                        content += `<p><strong>Message:</strong> ${escapeHtml(entry.message)}</p>`;

                        if (entry.data_preview && entry.data_preview !== "N/A") {
                            content += `<p><strong>Data Preview:</strong> <pre>${escapeHtml(entry.data_preview)}</pre></p>`;
                        }
                        if (entry.structured_data) {
                            try {
                                content += `<p><strong>Structured Data:</strong> <pre>${escapeHtml(JSON.stringify(entry.structured_data, null, 2))}</pre></p>`;
                            } catch (e) {
                                content += `<p><strong>Structured Data:</strong> <pre>${escapeHtml(String(entry.structured_data))}</pre> (Error displaying as JSON: ${e.message})</p>`;
                            }
                        }
                        logEntryDiv.innerHTML = content;
                        diagOrchestrationLogContainer.appendChild(logEntryDiv);
                        diagOrchestrationLogContainer.appendChild(document.createElement('hr'));
                    });
                }
            } else {
                diagOrchestrationLogContainer.innerHTML = '<p>Orchestration log not available or in unexpected format.</p>';
            }
        } catch (error) {
            console.error('Error fetching diagnostics:', error);
            diagOrchestrationLogContainer.innerHTML = `<p class="status-error">Failed to load diagnostics: ${escapeHtml(error.message)}</p>`;
            diagFinalErrorSpan.textContent = 'Error loading details.';
        }
    }

    if (diagnosticsModalCloseBtn) {
        diagnosticsModalCloseBtn.addEventListener('click', () => {
            if (diagnosticsModal) diagnosticsModal.style.display = 'none';
        });
    }

    // Event delegation for "View Diagnostics" buttons (attach to a static parent)
    // Using statusMessagesDiv as the parent where these buttons are appended.
    // If they can be appended elsewhere, adjust the parent selector.
    statusMessagesDiv.addEventListener('click', (event) => {
        if (event.target && event.target.classList.contains('view-diagnostics-btn')) {
            handleViewDiagnostics(event);
        }
    });
    // Also need to handle if the button is appended to a different status div,
    // e.g., if snippet generation also gets a diagnostics button.
    // For now, assuming it's mainly for full podcast generation status.
    // If #podcast-display or its children host the button, this needs to be adjusted.
    // The button is added to targetStatusDiv in displayPodcastGenerationOutcome, which is statusMessagesDiv.
    // So the above listener on statusMessagesDiv should cover it.

    // Close modal if user clicks outside of the modal content
    window.addEventListener('click', (event) => {
        if (event.target === diagnosticsModal) {
            diagnosticsModal.style.display = 'none';
        }
    });

});
