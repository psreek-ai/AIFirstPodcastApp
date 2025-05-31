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

    // MSE specific elements (from index.html)
    const mseAudioPlayer = document.getElementById('audio-player-mse');
    const streamingStatusDiv = document.getElementById('streaming-status');
    const generationProgressDisplay = document.getElementById('generation-progress-display');
    const retryStreamBtn = document.getElementById('retry-stream-btn');

    // State variables
    let currentSocket = null;
    let mediaSource = null;
    let sourceBuffer = null;
    let audioQueue = [];
    let isAppendingBuffer = false;
    // const ASF_NAMESPACE = '/api/v1/podcasts/stream'; // This seems unused as CPOA provides full URL
    let end_of_stream_received = false; // Tracks if server signaled end of stream

    let currentStreamId = null; // For retrying stream
    let currentAsfWebsocketUrl = null; // For retrying stream
    let simulatedProgressInterval = null; // For simulated generation progress

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

            // WebSocket Streaming Logic
            if (data.asf_websocket_url && data.final_audio_details && data.final_audio_details.stream_id) {
                console.log("ASF WebSocket URL found, attempting to stream:", data.asf_websocket_url, "Stream ID:", data.final_audio_details.stream_id);
                audioPlayer.classList.add('hidden');
                audioPlayer.src = '';
                mseAudioPlayer.classList.remove('hidden');
                initWebSocketStreaming(data.asf_websocket_url, data.final_audio_details.stream_id);
                updateStatus(targetStatusDiv.textContent + ' Attempting real-time audio stream.', 'success', targetStatusDiv);
            }
            // Fallback to direct audio URL
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
        console.log("Cleaning up MSE resources...");
        if (currentSocket) {
            currentSocket.disconnect(); // Disconnect socket
            currentSocket = null;
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
        currentAsfWebsocketUrl = null;
    }

    async function triggerPodcastGeneration(topic, statusDivId) {
        const targetStatusDiv = document.getElementById(statusDivId) || statusMessagesDiv;

        generateBtn.disabled = true; // Disable main generate button
        audioPlayer.classList.add('hidden'); // Hide direct player
        mseAudioPlayer.classList.add('hidden'); // Hide MSE player
        podcastDisplayDiv.classList.add('hidden'); // Hide entire podcast display area initially
        if(streamingStatusDiv) streamingStatusDiv.style.display = 'none';
        hideRetryButton();

        cleanupMSE(); // Clean up any previous MSE state

        startSimulatedGenerationProgress(topic); // Start simulated progress

        try {
            const response = await fetch('/api/v1/podcasts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ topic })
            });
            const responseOk = response.ok;
            const data = await response.json();
            // Pass targetStatusDivId to displayPodcastGenerationOutcome, it will handle it
            displayPodcastGenerationOutcome(topic, { ok: responseOk, status: response.status, data }, statusDivId);
        } catch (error) {
            console.error(`Error in triggerPodcastGeneration for topic "${topic}":`, error);
            stopSimulatedGenerationProgress();
            updateStatus(`Failed to start podcast generation for '${topic}'. Error: ${error.message || 'Network error or API unreachable.'}`, 'error', targetStatusDiv);
        } finally {
            generateBtn.disabled = false; // Re-enable main button
        }
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
    updateStatus("Enter a topic and click 'Generate Podcast', or choose a snippet below.", "info", statusMessagesDiv);
    fetchSnippets(); // Fetch snippets on page load
    cleanupMSE(); // Ensure clean MSE state on load
});
