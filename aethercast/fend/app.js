'use strict';

document.addEventListener('DOMContentLoaded', () => {
    // DOM Element References
    const topicInput = document.getElementById('topic-input');
    const generateBtn = document.getElementById('generate-btn');
    const statusMessagesDiv = document.getElementById('status-messages');
    const podcastDisplayDiv = document.getElementById('podcast-display');
    const podcastTopicTitle = document.getElementById('podcast-topic-title');
    const audioPlayer = document.getElementById('audio-player');
    const generationDetailsLog = document.getElementById('generation-details-log');

    // New DOM Element References for Snippets
    const podcastSnippetsSection = document.getElementById('podcast-snippets-section');
    const snippetListContainer = document.getElementById('snippet-list-container');
    const snippetStatusMessage = document.getElementById('snippet-status-message');
    const refreshSnippetsBtn = document.getElementById('refresh-snippets-btn');

    // Main podcast display (for direct URL or finished stream)
    const mainAudioPlayer = document.getElementById('audio-player'); // Renamed for clarity

    // MSE specific elements (assuming these are added to index.html)
    const mseAudioPlayer = document.getElementById('audio-player-mse');
    const streamingStatusDiv = document.getElementById('streaming-status');

    let currentSocket = null;
    let mediaSource = null;
    let sourceBuffer = null;
    let audioQueue = [];
    let isAppendingBuffer = false;
    const ASF_NAMESPACE = '/api/v1/podcasts/stream';


    let progressTimeouts = [];

    /**
     * Updates the status message display.
     * @param {string} message - The message to display.
     * @param {string} type - The type of message ('info', 'generating', 'success', 'error').
     */
    function updateStatus(message, type) {
        statusMessagesDiv.innerHTML = ''; // Clear current content
        const p = document.createElement('p');
        p.textContent = message;
        statusMessagesDiv.className = `status-messages status-${type}`; // Set class for styling
        statusMessagesDiv.appendChild(p);
    }

    /**
     * Clears all scheduled progress update timeouts.
     */
    function clearAllProgressTimeouts() {
        progressTimeouts.forEach(timeoutId => clearTimeout(timeoutId));
        progressTimeouts = [];
    }

    // Event Listener for Generate Button Click
    generateBtn.addEventListener('click', () => {
        const topic = topicInput.value.trim();

        // Validate Topic
        if (!topic) {
            updateStatus("Please enter a topic to generate a podcast.", "error");
            return;
        }

        // Initialize UI for Generation
        generateBtn.disabled = true;
        podcastDisplayDiv.classList.add('hidden');
        audioPlayer.src = '';
        podcastTopicTitle.textContent = '';
        generationDetailsLog.textContent = '';
            updateStatus(`Initiating podcast generation for: "${topic}"...`, 'info'); // Main status line

            // Clear any previous timeouts and hide main podcast display area
            clearAllProgressTimeouts();
            podcastDisplayDiv.classList.add('hidden');
            audioPlayer.src = '';
            podcastTopicTitle.textContent = '';
            generationDetailsLog.textContent = '';


        // API Call using fetch - This is for the main "Generate Podcast" button
        // We will create a new function for snippet-triggered generation
        // For now, let's keep this original function for the main button,
        // but adapt its UI updates to be more generic or use a specific area.

        // Simulate progress for the main generator button as before
        progressTimeouts.push(setTimeout(() => updateStatus(`Main Generator: Gathering info for "${topic}"...`, 'generating'), 2000));
        progressTimeouts.push(setTimeout(() => updateStatus(`Main Generator: Crafting script for "${topic}"...`, 'generating'), 5000));
        progressTimeouts.push(setTimeout(() => updateStatus(`Main Generator: Synthesizing audio for "${topic}"...`, 'generating'), 8000));

        // The actual fetch call
        fetch('/api/v1/podcasts', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ topic })
        })
        .then(response => {
            const responseOk = response.ok; // Store response.ok before consuming body
            return response.json().then(data => ({ ok: responseOk, status: response.status, data }));
        })
        .then(result => {
            clearAllProgressTimeouts();
            const { ok, status, data } = result;

            clearAllProgressTimeouts(); // Clear timeouts related to the main generator

            if (!ok) {
                const errorDetail = data.message || data.error || (status ? `Server error ${status}` : 'Unknown API error');
                updateStatus(`Main Generator API Error (${status}): ${errorDetail}`, 'error');
                generationDetailsLog.textContent = JSON.stringify(data, null, 2);
            } else {
                if (status === 201 && data.podcast_id && data.audio_url && data.generation_status === "completed") {
                    updateStatus(`Main Generator: Podcast "${data.topic || topic}" is ready!`, 'success');
                    podcastTopicTitle.textContent = data.topic || topic;
                    audioPlayer.src = data.audio_url;
                    generationDetailsLog.textContent = JSON.stringify(data.details || data, null, 2);
                    podcastDisplayDiv.classList.remove('hidden');
                    audioPlayer.load();
                } else { // Handle 200 OK with warnings/errors, or other unexpected 2xx
                    const message = data.message || (data.details && data.details.error_message) || 'Podcast generation finished with an unexpected status or missing data.';
                    updateStatus(`Main Generator Issue: ${message}`, 'error');
                    podcastTopicTitle.textContent = `Issue with: ${topic}`;
                    generationDetailsLog.textContent = JSON.stringify(data.details || data, null, 2);
                    podcastDisplayDiv.classList.remove('hidden');
                }
            }
        })
        .catch(error => {
            clearAllProgressTimeouts();
            const errorMessage = error.message || 'Network error or API unreachable.';
            updateStatus(`Main Generator API Request Failed: ${errorMessage}`, 'error');
            generationDetailsLog.textContent = error.toString();
        })
        .finally(() => {
            generateBtn.disabled = false;
            clearAllProgressTimeouts();
        });
    });

    // Function to display podcast generation status (can be called by snippet generation too)
    function displayPodcastGenerationOutcome(topic, result, targetStatusDiv, targetPlayerDivId, targetTitleId, targetDetailsId) {
        const { ok, status, data } = result;
        // const displayArea = document.getElementById(targetPlayerDivId);
        const titleEl = document.getElementById(targetTitleId);
        const detailsEl = document.getElementById(targetDetailsId);
        // const audioEl = displayArea ? displayArea.querySelector('audio') : null; // mainAudioPlayer for direct URL

        targetStatusDiv.className = 'status-messages';
        if(streamingStatusDiv) streamingStatusDiv.textContent = ''; // Clear streaming status

        if (!ok) {
            const errorDetail = data.message || data.error || (status ? `Server error ${status}` : 'Unknown API error');
            targetStatusDiv.textContent = `Failed to start podcast generation for '${topic}'. Error: ${errorDetail}`;
            targetStatusDiv.classList.add('status-error');
            if (detailsEl) detailsEl.textContent = JSON.stringify(data, null, 2);
            console.error("Error generating podcast:", data);
            if(mainAudioPlayer) mainAudioPlayer.classList.add('hidden');
            if(mseAudioPlayer) mseAudioPlayer.classList.add('hidden');

        } else {
            targetStatusDiv.textContent = `Podcast task for '${topic}' processed. Status: ${data.generation_status || status}.`;
            targetStatusDiv.classList.add(data.audio_url && data.generation_status === "completed" && data.asf_websocket_url ? 'status-success' : 'status-info');

            if (titleEl) titleEl.textContent = topic;
            if (detailsEl) detailsEl.textContent = JSON.stringify(data.details || data, null, 2);

            // WebSocket Streaming Logic
            if (data.asf_websocket_url && data.final_audio_details && data.final_audio_details.stream_id) {
                console.log("ASF WebSocket URL found, attempting to stream:", data.asf_websocket_url, "Stream ID:", data.final_audio_details.stream_id);
                if(mainAudioPlayer) mainAudioPlayer.classList.add('hidden'); // Hide direct player if streaming
                if(mainAudioPlayer) mainAudioPlayer.src = '';
                if(mseAudioPlayer) mseAudioPlayer.classList.remove('hidden');
                initWebSocketStreaming(data.asf_websocket_url, data.final_audio_details.stream_id);
                targetStatusDiv.textContent += ' Attempting real-time audio stream.';
            }
            // Fallback to direct audio URL if no WebSocket URL but audio_url exists (e.g., for non-ASF setups or completed files)
            else if (data.audio_url && data.generation_status === "completed") {
                console.log("Direct audio_url found, using standard audio player:", data.audio_url);
                if(mseAudioPlayer) mseAudioPlayer.classList.add('hidden'); // Hide MSE player
                if(mseAudioPlayer) mseAudioPlayer.src = '';
                if(mainAudioPlayer) {
                    mainAudioPlayer.src = data.audio_url;
                    mainAudioPlayer.load();
                    mainAudioPlayer.classList.remove('hidden');
                }
                targetStatusDiv.textContent += ' Playing directly via audio URL.';
            } else {
                if(mainAudioPlayer) mainAudioPlayer.classList.add('hidden');
                if(mseAudioPlayer) mseAudioPlayer.classList.add('hidden');
            }

            if (data.message && data.generation_status !== "completed") {
                targetStatusDiv.textContent += ` Server message: ${data.message}`;
            }
        }
        console.log("Podcast generation response:", data);
    }

    // WebSocket and MediaSource Extensions Logic
    function initWebSocketStreaming(wsBaseUrl, streamId) {
        if (currentSocket) {
            currentSocket.disconnect();
            currentSocket = null;
        }
        if (mediaSource && mediaSource.readyState === 'open') {
            try { mediaSource.endOfStream(); } catch (e) { console.warn("Error ending previous MediaSource stream:", e); }
        }
        audioQueue = [];
        isAppendingBuffer = false;

        const fullWsUrl = wsBaseUrl.startsWith('ws') ? wsBaseUrl + ASF_NAMESPACE : `ws://${wsBaseUrl}` + ASF_NAMESPACE;
        // If wsBaseUrl already includes the namespace (which it might if it's `ASF_WEBSOCKET_BASE_URL` from CPOA)
        // then this logic might need adjustment. For now, assuming wsBaseUrl is just `ws://host:port`.
        // CPOA's ASF_WEBSOCKET_BASE_URL is `ws://localhost:5006/api/v1/podcasts/stream`
        // So, it already includes the namespace.

        // Corrected socket connection:
        // The namespace is part of the URL for client if server uses namespaces like Flask-SocketIO does.
        // If ASF_WEBSOCKET_BASE_URL from CPOA is `ws://localhost:5006/api/v1/podcasts/stream`
        // then `io(ASF_WEBSOCKET_BASE_URL)` should be correct.
        // If ASF_WEBSOCKET_BASE_URL is just `ws://localhost:5006`, then `io(ASF_WEBSOCKET_BASE_URL + ASF_NAMESPACE)`
        // CPOA provides `ws://localhost:5006/api/v1/podcasts/stream` as `asf_websocket_url` in its final result,
        // which is what `wsBaseUrl` becomes here. So, `io(wsBaseUrl)` is correct.

        console.log(`Attempting to connect to WebSocket at: ${wsBaseUrl} for stream ID: ${streamId}`);
        currentSocket = io(wsBaseUrl, {
            // path: ASF_NAMESPACE, // path is for specific sub-paths on the server, not for namespace usually.
                                 // Namespace is part of the connection URL or specified in connect method.
            reconnectionAttempts: 3
        });

        updateStreamingStatus(`Connecting to audio stream for ${streamId}...`);

        currentSocket.on('connect', () => {
            console.log('ASF WebSocket connected! SID:', currentSocket.id);
            updateStreamingStatus('Connected. Joining stream...');
            currentSocket.emit('join_stream', { stream_id: streamId });
        });

        mediaSource = new MediaSource();
        if(mseAudioPlayer) mseAudioPlayer.src = URL.createObjectURL(mediaSource);

        mediaSource.addEventListener('sourceopen', () => handleMediaSourceOpen(streamId));

        currentSocket.on('disconnect', (reason) => {
            console.log('ASF WebSocket disconnected:', reason);
            updateStreamingStatus(`Stream disconnected: ${reason}.`);
            cleanupMSE();
        });
        currentSocket.on('error', (error) => {
            console.error('ASF WebSocket error:', error);
            updateStreamingStatus(`Stream error: ${error.message || error}.`);
            cleanupMSE();
        });
         currentSocket.on('connect_error', (error) => {
            console.error('ASF WebSocket connection error:', error);
            updateStreamingStatus(`Failed to connect to stream: ${error.message || error}.`);
            cleanupMSE();
        });
    }

    function handleMediaSourceOpen(streamId) {
        console.log('MediaSource opened. Stream ID:', streamId);
        updateStreamingStatus('MediaSource ready. Waiting for audio data...');
        try {
            // Assuming MP3 audio as per VFA's default configuration.
            // This needs to match the actual audio format streamed by ASF.
            sourceBuffer = mediaSource.addSourceBuffer('audio/mpeg');
            sourceBuffer.addEventListener('updateend', () => {
                isAppendingBuffer = false;
                appendNextChunk(); // Try to append next chunk if any
            });
            sourceBuffer.addEventListener('error', (ev) => {
                console.error('SourceBuffer error:', ev);
                updateStreamingStatus('Error with audio buffer.');
            });
        } catch (e) {
            console.error('Error adding SourceBuffer:', e);
            updateStreamingStatus(`Error setting up audio buffer: ${e.message}`);
            return;
        }

        currentSocket.on('audio_chunk', (data) => {
            // console.log('Received audio_chunk, type:', typeof data, 'instanceof ArrayBuffer:', data instanceof ArrayBuffer, 'size:', data.byteLength);
            if (data instanceof ArrayBuffer) { // Socket.IO client typically delivers binary as ArrayBuffer
                audioQueue.push(data);
                appendNextChunk();
            } else {
                console.warn("Received audio_chunk that is not an ArrayBuffer:", data);
            }
        });

        currentSocket.on('audio_control', (message) => {
            console.log('Received audio_control:', message);
            if (message.event === 'start_of_stream') {
                updateStreamingStatus('Audio stream started...');
            } else if (message.event === 'end_of_stream') {
                updateStreamingStatus('End of stream signal received. Finishing up...');
                if (audioQueue.length === 0 && !isAppendingBuffer && sourceBuffer && !sourceBuffer.updating) {
                    if (mediaSource.readyState === 'open') {
                        mediaSource.endOfStream();
                        console.log('MediaSource stream ended.');
                    }
                } else {
                    // Wait for queue to drain and buffer to finish updating
                    const endStreamInterval = setInterval(() => {
                        if (audioQueue.length === 0 && !isAppendingBuffer && sourceBuffer && !sourceBuffer.updating) {
                            clearInterval(endStreamInterval);
                            if (mediaSource.readyState === 'open') {
                                mediaSource.endOfStream();
                                console.log('MediaSource stream ended after queue drain.');
                            }
                        }
                    }, 100);
                }
                // currentSocket.disconnect(); // Optionally disconnect after stream ends
            }
        });

        currentSocket.on('stream_error', (error) => {
            console.error('Received stream_error from ASF:', error);
            updateStreamingStatus(`Error from stream: ${error.message}`);
            cleanupMSE();
            if(currentSocket) currentSocket.disconnect();
        });
    }

    function appendNextChunk() {
        if (!isAppendingBuffer && audioQueue.length > 0 && sourceBuffer && !sourceBuffer.updating && mediaSource.readyState === 'open') {
            isAppendingBuffer = true;
            const chunk = audioQueue.shift();
            try {
                // console.log("Appending buffer, size:", chunk.byteLength);
                sourceBuffer.appendBuffer(chunk);
                updateStreamingStatus(`Buffering audio... Queue size: ${audioQueue.length}`);
            } catch (e) {
                console.error('Error appending buffer:', e);
                updateStreamingStatus(`Error buffering audio: ${e.message}.`);
                isAppendingBuffer = false; // Reset flag on error
                 // If quota exceeded, might need more complex handling like removing old buffer ranges
                if (e.name === 'QuotaExceededError') {
                    // Simple cleanup for now, more advanced would remove ranges
                    audioQueue = [];
                    if (mediaSource.readyState === 'open') mediaSource.endOfStream();
                }
            }
        }
    }

    function updateStreamingStatus(message) {
        if (streamingStatusDiv) {
            streamingStatusDiv.textContent = message;
            console.log("Streaming Status:", message);
        } else {
            console.log("Streaming Status (no div):", message);
        }
    }

    function cleanupMSE() {
        if (mediaSource && mediaSource.readyState === 'open') {
            try {
                // Remove all source buffers if any
                if (sourceBuffer) {
                     if (!sourceBuffer.updating) {
                        mediaSource.removeSourceBuffer(sourceBuffer);
                     } else {
                        sourceBuffer.addEventListener('updateend', () => {
                           if (mediaSource.readyState === 'open') mediaSource.removeSourceBuffer(sourceBuffer);
                        });
                     }
                }
                // mediaSource.endOfStream(); // Call only if stream was successfully started and chunks were appended
            } catch (e) {
                console.warn("Error during MSE cleanup:", e);
            }
        }
        sourceBuffer = null;
        isAppendingBuffer = false;
        audioQueue = [];
        if(mseAudioPlayer && mseAudioPlayer.src) {
            URL.revokeObjectURL(mseAudioPlayer.src); // Revoke the object URL
            mseAudioPlayer.removeAttribute('src');    // Remove src attribute
            mseAudioPlayer.load();                    // Reset the audio element
        }
    }

    async function triggerPodcastGeneration(topic, statusDivId) {
        const statusDiv = document.getElementById(statusDivId) || podcastGenerationStatusDiv || statusMessagesDiv;
        statusDiv.textContent = `Generating podcast for '${topic}'... Please wait.`;
        statusDiv.className = 'status-messages status-generating';
        if (streamingStatusDiv) streamingStatusDiv.textContent = ''; // Clear previous streaming status
        cleanupMSE(); // Clean up any previous MSE state

        try {
            const response = await fetch('/api/v1/podcasts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ topic })
            });
            const responseOk = response.ok;
            const data = await response.json();
            displayPodcastGenerationOutcome(topic, { ok: responseOk, status: response.status, data }, statusDiv, 'podcast-display', 'podcast-topic-title', 'generation-details-log');
        } catch (error) {
            console.error(`Error in triggerPodcastGeneration for topic "${topic}":`, error);
            statusDiv.textContent = `Failed to start podcast generation for '${topic}'. Error: ${error.message || 'Network error or API unreachable.'}`;
            statusDiv.className = 'status-messages status-error';
        }
    }

    async function fetchSnippets() {
        snippetStatusMessage.textContent = 'Loading fresh snippets...';
        snippetStatusMessage.className = 'status-messages status-info';
        snippetStatusMessage.classList.remove('hidden');
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
                snippetStatusMessage.classList.add('hidden');
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
                snippetStatusMessage.textContent = 'No snippets available at the moment. Try refreshing!';
            }
        } catch (error) {
            console.error('Error fetching snippets:', error);
            snippetStatusMessage.textContent = `Error loading snippets: ${error.message}`;
            snippetStatusMessage.className = 'status-messages status-error';
        }
    }

    // Event listener for "Refresh Snippets" button
    if (refreshSnippetsBtn) {
        refreshSnippetsBtn.addEventListener('click', fetchSnippets);
    } else {
        console.warn("#refresh-snippets-btn not found in HTML.");
    }

    // Event delegation for "Generate Podcast" buttons on snippets
    snippetListContainer.addEventListener('click', (event) => {
        if (event.target && event.target.classList.contains('generate-podcast-snippet-btn')) {
            const topic = event.target.dataset.topic;
            if (topic) {
                // Use a general status display area for snippet-triggered podcast generation for now
                // Or create a dedicated one if preferred.
                triggerPodcastGeneration(topic, 'status-messages');
                 // Scroll to the main status/output area
                document.getElementById('status-messages').scrollIntoView({ behavior: 'smooth' });
            } else {
                console.error("No topic found on snippet button.");
                (document.getElementById('status-messages') || podcastGenerationStatusDiv).textContent = "Error: Could not determine topic from snippet button.";
                (document.getElementById('status-messages') || podcastGenerationStatusDiv).className = 'status-messages status-error';
            }
        }
    });

    // Initial calls
    updateStatus("Please enter a topic and click 'Generate Podcast' to begin, or explore snippets below.", "info");
    fetchSnippets(); // Fetch snippets on page load
});
