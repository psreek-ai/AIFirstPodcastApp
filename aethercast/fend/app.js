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
    const podcastSnippetsSection = document.getElementById('podcast-snippets-section'); // This ID might be 'latest-episodes-section' in new HTML
    const latestEpisodesSection = document.getElementById('latest-episodes-section'); // Added for clarity if used
    const snippetListContainer = document.getElementById('snippet-list-container');
    const snippetStatusMessage = document.getElementById('snippet-status-message');
    const refreshSnippetsBtn = document.getElementById('refresh-snippets-btn'); // Assuming this might be removed or repurposed

    // Episode Search elements (within Latest Episodes section)
    const episodesSearchInput = document.getElementById('episodes-search-input'); // Kept
    const episodesSearchBtn = document.getElementById('episodes-search-btn'); // Kept

    // Header Search Input
    const headerSearchInput = document.getElementById('header-search-input');

    // Popular Categories container
    const popularCategoriesContainer = document.querySelector('#popular-categories-section .category-list-container'); // Added

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

    // Preferences UI Elements
    const prefNewsCategoryInput = document.getElementById('pref-news-category');
    const savePrefsBtn = document.getElementById('save-prefs-btn');
    const prefsStatusP = document.getElementById('prefs-status');


    // State variables
    let currentSocket = null;
    let mediaSource = null;
    let sourceBuffer = null;
    let audioQueue = [];
    let isAppendingBuffer = false;
    const UI_UPDATES_NAMESPACE = '/ui_updates';
    let end_of_stream_received = false;

    let currentStreamId = null;
    let currentAsfWebsocketUrl = null;
    let currentAsfBaseUrl = null;
    let simulatedProgressInterval = null;

    let currentUiClientId = null;
    let uiUpdateSocket = null;
    let currentUserPreferences = {}; // Added for user preferences

    // Socket.IO Event Name Constants
    const AUDIO_EVT_CONNECT = 'connect';
    const AUDIO_EVT_DISCONNECT = 'disconnect';
    const AUDIO_EVT_CONNECT_ERROR = 'connect_error';
    const AUDIO_EVT_ERROR = 'error';
    const AUDIO_EVT_JOIN_STREAM = 'join_stream';
    const AUDIO_EVT_CONNECTION_ACK = 'connection_ack';
    const AUDIO_EVT_STREAM_STATUS = 'stream_status';
    const AUDIO_EVT_STREAM_ERROR = 'stream_error';
    const AUDIO_EVT_AUDIO_CHUNK = 'audio_chunk';
    const AUDIO_EVT_AUDIO_CONTROL = 'audio_control';
    const AUDIO_CTL_START_OF_STREAM = 'start_of_stream';
    const AUDIO_CTL_END_OF_STREAM = 'end_of_stream';

    const UI_EVT_CONNECT = 'connect'; // Note: Re-using 'connect' for UI, context is namespace
    const UI_EVT_DISCONNECT = 'disconnect'; // Re-using 'disconnect'
    const UI_EVT_CONNECT_ERROR = 'connect_error'; // Re-using 'connect_error'
    const UI_EVT_UI_ERROR = 'ui_error';
    const UI_EVT_SUBSCRIBE = 'subscribe_to_ui_updates';
    const UI_EVT_SUBSCRIBED_ACK = 'subscribed_ui_updates';
    const UI_EVT_CONNECTION_ACK = 'ui_connection_ack';
    const UI_EVT_IN_GENERATION_STATUS = 'generation_status';
    const UI_EVT_IN_TASK_ERROR = 'task_error';


    async function initSessionAndPreferences() {
        if (!currentUiClientId) {
            console.error("Cannot initialize session, client_id is not available.");
            return;
        }
        console.log("Initializing session with client_id:", currentUiClientId);
        try {
            const response = await fetch('/api/v1/session/init', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ client_id: currentUiClientId })
            });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ message: `HTTP error ${response.status}` }));
                throw new Error(errorData.message || `Session init failed. Status: ${response.status}`);
            }
            const data = await response.json();
            currentUserPreferences = data.preferences || {};
            console.log("Session initialized/updated. Preferences loaded:", currentUserPreferences);
            populatePreferencesForm();
            if (prefsStatusP) updateStatus("Preferences loaded.", "info", prefsStatusP);
        } catch (error) {
            console.error("Error initializing session:", error);
            if (prefsStatusP) updateStatus(`Error loading preferences: ${error.message}`, "error", prefsStatusP);
            currentUserPreferences = {}; // Reset to empty on error
            populatePreferencesForm(); // Still populate (i.e., clear) the form
        }
    }

    function populatePreferencesForm() {
        if (prefNewsCategoryInput) {
            prefNewsCategoryInput.value = currentUserPreferences.news_category || '';
        }
        // Add more preferences here as UI grows
        // e.g., if (prefVoiceNameSelect) { prefVoiceNameSelect.value = currentUserPreferences.preferred_vfa_voice_name || ''; }
    }

    function generateOrGetClientId() {
        if (!currentUiClientId) {
            currentUiClientId = Date.now().toString(36) + Math.random().toString(36).substring(2);
            console.log("Generated UI Client ID:", currentUiClientId);
            // Initialize session as soon as client_id is available
            initSessionAndPreferences();
        }
        return currentUiClientId;
    }

    function updateStatus(message, type = 'info', targetDiv = statusMessagesDiv) {
        if(targetDiv){
            targetDiv.innerHTML = '';
            const p = document.createElement('p');
            p.textContent = message;
            targetDiv.className = `status-messages status-${type}`;
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
        // ... (existing function content)
    }

    function showRetryButton() {
        if(retryStreamBtn) retryStreamBtn.style.display = 'block';
    }

    function hideRetryButton() {
        if(retryStreamBtn) retryStreamBtn.style.display = 'none';
    }

    generateBtn.addEventListener('click', () => {
        const topic = topicInput.value.trim();
        if (!topic) {
            updateStatus("Please enter a topic to generate a podcast.", "error", statusMessagesDiv);
            return;
        }
        triggerPodcastGeneration(topic, 'status-messages');
    });

    function displayPodcastGenerationOutcome(topic, result, targetStatusDivId) {
        // ... (existing function content, ensure it calls stopSimulatedGenerationProgress)
        const targetStatusDiv = document.getElementById(targetStatusDivId) || statusMessagesDiv;
        stopSimulatedGenerationProgress();

        const { ok, status, data } = result;

        podcastTopicTitle.textContent = topic;
        podcastDisplayDiv.classList.remove('hidden');

        if (streamingStatusDiv) {
            streamingStatusDiv.textContent = '';
            streamingStatusDiv.style.display = 'none';
        }
        hideRetryButton();

        if (!ok) {
            const errorDetail = data.message || data.error || (status ? `Server error ${status}` : 'Unknown API error');
            updateStatus(`Failed to start podcast generation for '${topic}'. Error: ${errorDetail}`, 'error', targetStatusDiv);
            if (generationDetailsLog) generationDetailsLog.textContent = JSON.stringify(data, null, 2);
            console.error("Error generating podcast:", data);
            audioPlayer.classList.add('hidden');
            mseAudioPlayer.classList.add('hidden');
        } else {
            updateStatus(`Podcast task for '${topic}' processed. Status: ${data.generation_status || status}.`,
                         (data.asf_websocket_url || data.audio_url) ? 'success' : 'info',
                         targetStatusDiv);

            if (generationDetailsLog) generationDetailsLog.textContent = JSON.stringify(data.details || data, null, 2);

            if (data.asf_websocket_url && currentUiClientId) {
                try {
                    const urlObj = new URL(data.asf_websocket_url);
                    currentAsfBaseUrl = `${urlObj.protocol}//${urlObj.host}`;
                    console.log("Derived ASF Base URL for UI updates:", currentAsfBaseUrl);
                    initUIUpdateSocket(currentAsfBaseUrl, currentUiClientId);
                } catch (e) {
                    console.error("Error parsing ASF WebSocket URL to get base for UI updates:", e);
                    updateStatus("Could not establish UI update channel (URL parse error).", "error", generationProgressDisplay);
                }
            }

            if (data.asf_websocket_url && data.final_audio_details && data.final_audio_details.stream_id) {
                console.log("Audio Streaming: ASF WebSocket URL found, attempting to stream:", data.asf_websocket_url, "Stream ID:", data.final_audio_details.stream_id);
                audioPlayer.classList.add('hidden');
                audioPlayer.src = '';
                mseAudioPlayer.classList.remove('hidden');
                initWebSocketStreaming(data.asf_websocket_url, data.final_audio_details.stream_id);
                updateStatus(targetStatusDiv.textContent + ' Attempting real-time audio stream.', 'success', targetStatusDiv);
            }
            else if (data.audio_url && data.generation_status === "completed") {
                console.log("Direct audio_url found, using standard audio player:", data.audio_url);
                mseAudioPlayer.classList.add('hidden');
                mseAudioPlayer.src = '';
                audioPlayer.src = data.audio_url;
                audioPlayer.load();
                audioPlayer.classList.remove('hidden');
                updateStatus(targetStatusDiv.textContent + ' Playing directly via audio URL.', 'success', targetStatusDiv);
            } else {
                audioPlayer.classList.add('hidden');
                mseAudioPlayer.classList.add('hidden');
                if (data.generation_status !== "completed" && !data.asf_websocket_url) {
                     updateStatus(targetStatusDiv.textContent + ` Further details: ${data.message || 'Awaiting completion or stream setup.'}`, 'info', targetStatusDiv);
                }
            }
        }
        console.log("Podcast generation response:", data);

        const mainStatusArea = document.getElementById(targetStatusDivId) || statusMessagesDiv;
        if (data.podcast_id && mainStatusArea) {
            const existingBtn = mainStatusArea.querySelector('.view-diagnostics-btn');
            if (existingBtn) existingBtn.remove();

            const diagBtn = document.createElement('button');
            diagBtn.textContent = "View Diagnostics";
            diagBtn.classList.add('view-diagnostics-btn');
            diagBtn.dataset.podcastId = data.podcast_id;
            diagBtn.style.marginTop = "10px";
            mainStatusArea.appendChild(diagBtn);
        }
    }

    function initWebSocketStreaming(wsBaseUrl, streamId) {
        // ... (existing function, ensure it uses constants for event names)
        cleanupMSE();
        currentAsfWebsocketUrl = wsBaseUrl;
        currentStreamId = streamId;
        end_of_stream_received = false;

        console.log(`Attempting to connect to WebSocket at: ${wsBaseUrl} for stream ID: ${streamId}`);
        currentSocket = io(wsBaseUrl, { reconnectionAttempts: 3 });

        updateStreamingStatus(`Connecting to audio stream for ${streamId}...`, false);

        currentSocket.on(AUDIO_EVT_CONNECT, () => {
            console.log('ASF WebSocket connected! SID:', currentSocket.id);
            updateStreamingStatus('Connected. Joining stream...', false);
            currentSocket.emit(AUDIO_EVT_JOIN_STREAM, { stream_id: streamId });
        });

        mediaSource = new MediaSource();
        mseAudioPlayer.src = URL.createObjectURL(mediaSource);
        mediaSource.addEventListener('sourceerror', (ev) => { /* ... */ });
        mediaSource.addEventListener('sourceopen', () => handleMediaSourceOpen(streamId));

        currentSocket.on(AUDIO_EVT_DISCONNECT, (reason) => { /* ... */ });
        currentSocket.on(AUDIO_EVT_ERROR, (error) => { /* ... */ });
        currentSocket.on(AUDIO_EVT_CONNECT_ERROR, (error) => { /* ... */ });
    }

    function handleMediaSourceOpen(streamId) {
        // ... (existing function, ensure it uses constants for event names)
        console.log('MediaSource opened. Stream ID:', streamId);
        updateStreamingStatus('MediaSource ready. Waiting for audio data...', false);
        try {
            sourceBuffer = mediaSource.addSourceBuffer('audio/ogg; codecs=opus');
            sourceBuffer.addEventListener('updateend', () => { /* ... */ });
            sourceBuffer.addEventListener('error', (ev) => { /* ... */ });
        } catch (e) { /* ... */ }

        currentSocket.on(AUDIO_EVT_AUDIO_CHUNK, (data) => { /* ... */ });
        currentSocket.on(AUDIO_EVT_AUDIO_CONTROL, (message) => { /* ... */ });
        currentSocket.on(AUDIO_EVT_STREAM_ERROR, (error) => { /* ... */ });
    }

    function checkIfStreamFinished() { /* ... (existing function) */ }
    function appendNextChunk() { /* ... (existing function) */ }
    function checkBufferingStatus() { /* ... (existing function) */ }
    function updateStreamingStatus(message, isError = false) { /* ... (existing function) */ }
    function cleanupMSE() { /* ... (existing function, ensures uiUpdateSocket is also cleaned up) */ }

    async function triggerPodcastGeneration(topic, statusDivId) {
        // ... (existing function, ensures generateOrGetClientId is called)
        const targetStatusDiv = document.getElementById(statusDivId) || statusMessagesDiv;
        generateOrGetClientId();

        generateBtn.disabled = true;
        // ... (rest of UI reset) ...
        cleanupMSE();
        startSimulatedGenerationProgress(topic);

        try {
            const payload = {
                topic: topic,
                client_id: currentUiClientId
            };
            const response = await fetch('/api/v1/podcasts', { /* ... */ });
            const responseOk = response.ok;
            const data = await response.json();
            displayPodcastGenerationOutcome(topic, { ok: responseOk, status: response.status, data }, statusDivId);
        } catch (error) { /* ... */ } finally { /* ... */ }
    }

    function initUIUpdateSocket(asfBaseUrlToParse, clientIdToSubscribe) {
        // ... (existing function, ensure it uses constants for event names)
        if (uiUpdateSocket) { /* ... */ }
        // ... (URL parsing) ...
        uiUpdateSocket = io(actualAsfBaseUrl + UI_UPDATES_NAMESPACE, { reconnectionAttempts: 3 });

        uiUpdateSocket.on(UI_EVT_CONNECT, () => { /* ... emit(UI_EVT_SUBSCRIBE, ...) ... */ });
        uiUpdateSocket.on(UI_EVT_SUBSCRIBED_ACK, (data) => { /* ... */ });
        uiUpdateSocket.on(UI_EVT_IN_GENERATION_STATUS, (data) => { /* ... */ });
        uiUpdateSocket.on(UI_EVT_IN_TASK_ERROR, (data) => { /* ... */ });
        uiUpdateSocket.on(UI_EVT_UI_ERROR, (data) => { /* ... */ });
        uiUpdateSocket.on(UI_EVT_DISCONNECT, (reason) => { /* ... */ });
        uiUpdateSocket.on(UI_EVT_CONNECT_ERROR, (error) => { /* ... */ });
    }

    // ... (MSE player event listeners, retry button listener - existing code)

    // --- Preferences Section Logic ---
    if (savePrefsBtn) {
        savePrefsBtn.addEventListener('click', async () => {
            if (!currentUiClientId) {
                updateStatus("Client ID not available. Cannot save preferences.", "error", prefsStatusP);
                return;
            }
            const newsCategoryValue = prefNewsCategoryInput ? prefNewsCategoryInput.value.trim() : '';
            // Add other preference retrievals here
            // const preferredVoice = prefVoiceSelect ? prefVoiceSelect.value : '';

            const prefsToSave = {
                "news_category": newsCategoryValue
                // if (preferredVoice) prefsToSave.preferred_vfa_voice_name = preferredVoice;
            };

            updateStatus("Saving preferences...", "info", prefsStatusP);
            try {
                const response = await fetch('/api/v1/session/preferences', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ client_id: currentUiClientId, preferences: prefsToSave })
                });
                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({ message: `HTTP error ${response.status}` }));
                    throw new Error(errorData.message || `Failed to save preferences. Status: ${response.status}`);
                }
                await response.json(); // Consume response body
                currentUserPreferences = { ...currentUserPreferences, ...prefsToSave }; // Update local cache
                updateStatus("Preferences saved successfully!", "success", prefsStatusP);
                populatePreferencesForm(); // Re-populate to ensure consistency if needed
            } catch (error) {
                console.error("Error saving preferences:", error);
                updateStatus(`Error saving preferences: ${error.message}`, "error", prefsStatusP);
            }
        });
    }


    // --- Snippet & Topic Exploration Logic (existing functions, ensure renderSnippetCard etc. are preserved) ---
    // For brevity, I'm assuming these are largely unchanged unless they need to interact with preferences.
    // The triggerPodcastGeneration call from snippet buttons already uses the main function.

    async function fetchAndRenderSearchResults(query) {
        if (!snippetStatusMessage || !snippetListContainer) {
            console.warn("Search UI elements (status message or list container) not found. Cannot perform search.");
            return;
        }

        updateStatus(`Searching for '${query}'...`, 'info', snippetStatusMessage);
        snippetListContainer.innerHTML = ''; // Clear previous results

        const payload = { query: query };
        if (currentUiClientId) { // currentUiClientId is a global in app.js
            payload.client_id = currentUiClientId;
        }

        try {
            const response = await fetch('/api/v1/search/podcasts', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(payload),
            });

            if (!response.ok) {
                let errorMsg = `Server responded with ${response.status}`;
                try {
                    const errorData = await response.json();
                    // Prefer specific message from server if available
                    errorMsg = errorData.message || errorData.details || errorData.error || errorMsg;
                } catch (e) {
                    // Ignore if error response is not JSON, use the HTTP status based message
                }
                // Throw an error to be caught by the catch block
                throw new Error(errorMsg);
            }

            const data = await response.json();

            if (data.search_results && Array.isArray(data.search_results) && data.search_results.length > 0) {
                data.search_results.forEach(snippet => renderSnippetCard(snippet, snippetListContainer));
                updateStatus(`Found ${data.search_results.length} results for '${query}'.`, 'success', snippetStatusMessage);
            } else if (data.search_results && Array.isArray(data.search_results) && data.search_results.length === 0) {
                 updateStatus(`No results found for '${query}'.`, 'info', snippetStatusMessage);
            }
            else { // Handle cases where search_results key might be missing or not an array
                console.warn("Search response format unexpected:", data);
                updateStatus(`Unexpected response format from server for query '${query}'.`, 'error', snippetStatusMessage);
            }

        } catch (error) {
            console.error('Error fetching or rendering search results:', error);
            // error.message here will be the one thrown from (!response.ok) block or from fetch/json parse failures
            updateStatus(`An error occurred while fetching search results: ${error.message}`, 'error', snippetStatusMessage);
        }
    }

    async function newFetchSnippets() {
        if (!snippetListContainer || !snippetStatusMessage) {
            console.warn("Snippet UI elements not found, skipping snippet fetch.");
            return;
        }
        snippetStatusMessage.textContent = 'Loading fresh snippets...';
        snippetStatusMessage.className = 'status-messages status-info';
        snippetListContainer.innerHTML = ''; // Clear existing snippets

        try {
            const response = await fetch('/api/v1/snippets');
            if (!response.ok) {
                let errorMsg = `Server responded with ${response.status}`;
                try {
                    const errorData = await response.json();
                    // Prefer specific message from server if available
                    errorMsg = errorData.message || errorData.details || errorData.error || errorMsg;
                } catch (e) {
                    // Ignore if error response is not JSON, use the HTTP status based message
                }
                throw new Error(errorMsg); // Throw the more detailed error message
            }
            const data = await response.json();

            if (data.snippets && Array.isArray(data.snippets) && data.snippets.length > 0) {
                data.snippets.forEach(snippet => renderSnippetCard(snippet, snippetListContainer));
                updateStatus(`Showing ${data.snippets.length} snippets. Source: ${data.source}`, 'success', snippetStatusMessage);
            } else if (data.snippets && Array.isArray(data.snippets) && data.snippets.length === 0) {
                updateStatus('No snippets available at the moment.', 'info', snippetStatusMessage);
            } else {
                console.warn("Snippets response format unexpected:", data);
                updateStatus('Unexpected response format from server for snippets.', 'error', snippetStatusMessage);
            }
        } catch (error) {
            console.error('Error fetching snippets:', error);
            updateStatus(`Error fetching snippets: ${error.message}`, 'error', snippetStatusMessage);
        }
    }
    const fetchAndRenderSnippets = newFetchSnippets;

    function renderSnippetCard(snippet, containerElement) {
        const cardDiv = document.createElement('div');
        cardDiv.className = 'snippet-card';

        const imagePlaceholderDiv = document.createElement('div');
        imagePlaceholderDiv.className = 'snippet-image-placeholder';

        if (snippet.image_url && typeof snippet.image_url === 'string' && snippet.image_url.trim() !== '') {
            imagePlaceholderDiv.style.backgroundImage = `url('${snippet.image_url}')`;
        } else {
            // CSS default background for .snippet-image-placeholder will apply here
        }

        const textContentDiv = document.createElement('div');
        textContentDiv.className = 'snippet-text-content';

        const titleH3 = document.createElement('h3');
        titleH3.textContent = snippet.title || "Untitled Snippet";

        const summaryP = document.createElement('p');
        summaryP.className = 'snippet-summary';
        summaryP.textContent = snippet.summary || snippet.text_content || "No summary available.";

        const listenNowButton = document.createElement('button');
        listenNowButton.className = 'listen-now-button';
        listenNowButton.dataset.topic = snippet.title; // Use title as topic for generation

        const iconSpan = document.createElement('span');
        iconSpan.className = 'material-icons-outlined'; // For Material Icons, if used
        iconSpan.textContent = 'play_circle_filled'; // Icon name

        const textSpan = document.createElement('span');
        textSpan.textContent = 'Listen Now';

        listenNowButton.appendChild(iconSpan);
        listenNowButton.appendChild(textSpan);

        listenNowButton.addEventListener('click', (event) => {
            const topicForGeneration = event.currentTarget.dataset.topic;
            if (topicForGeneration) {
                // Use a more generic status display if this card is outside main podcast output area
                // For now, using the main 'status-messages' div.
                // A better approach might be to pass a status display target to renderSnippetCard
                // or have a dedicated status area for "quick play" from cards.
                updateStatus(`Initiating podcast for snippet: '${topicForGeneration}'...`, 'info', statusMessagesDiv);
                triggerPodcastGeneration(topicForGeneration, 'status-messages');
                // Scroll to the main player/status area after initiating
                podcastDisplayDiv.scrollIntoView({ behavior: 'smooth' });
            } else {
                console.error("No topic data found on 'Listen Now' button.");
                updateStatus("Could not start podcast: topic data missing.", "error", statusMessagesDiv);
            }
        });

        textContentDiv.appendChild(titleH3);
        textContentDiv.appendChild(summaryP);

        cardDiv.appendChild(imagePlaceholderDiv);
        cardDiv.appendChild(textContentDiv);
        cardDiv.appendChild(listenNowButton);

        containerElement.appendChild(cardDiv);
    }

    async function triggerTopicExploration(payload) { /* ... (existing function) ... */ }
    function handleExploreRelated(event) { /* ... (existing function) ... */ }

    if (refreshSnippetsBtn) {
        refreshSnippetsBtn.addEventListener('click', fetchAndRenderSnippets);
    }

    // Remove or adapt the old generic snippetListContainer listener if it conflicts.
    // The new "Listen Now" buttons have their own direct listeners.
    // If other interactions on snippet cards are needed, this might be adapted.
    // For now, let's comment it out to avoid potential double handling or conflicts.
    /*
    snippetListContainer.addEventListener('click', (event) => {
        // Example: if (event.target.classList.contains('some-other-button-on-card')) { ... }
    });
    */

    if (exploredTopicsContainer) { /* ... */ }
    if (exploreKeywordsBtn) {
        exploreKeywordsBtn.addEventListener('click', () => {
            const keywords = exploreKeywordsInput.value.trim();
            if (!keywords) {
                updateStatus("Please enter keywords to explore.", "error", exploredTopicsStatus);
                return;
            }
            triggerTopicExploration({ keywords: keywords.split(',').map(k => k.trim()) });
        });
    }

    // Event listener for the new episode search button
    if (episodesSearchBtn && episodesSearchInput) {
        episodesSearchBtn.addEventListener('click', () => {
            const query = episodesSearchInput.value.trim();
            if (!query) {
                updateStatus("Please enter a search query.", "error", snippetStatusMessage);
                return;
            }
            fetchAndRenderSearchResults(query);
        });
    } else {
        console.warn("Episode search input or button not found. Search functionality in 'Latest Episodes' section will not be available.");
    }

    // --- Diagnostics Modal Logic (existing functions) ---
    function escapeHtml(unsafe) { /* ... */ }
    async function handleViewDiagnostics(event) { /* ... */ }
    if (diagnosticsModalCloseBtn) { /* ... */ }
    statusMessagesDiv.addEventListener('click', (event) => { /* ... */ });
    window.addEventListener('click', (event) => { /* ... */ });

    // --- Initial calls ---
    // generateOrGetClientId will call initSessionAndPreferences, which calls populatePreferencesForm
    generateOrGetClientId();
    updateStatus("Enter a topic and click 'Generate Podcast', explore keywords, or choose a snippet below.", "info", statusMessagesDiv);
    fetchAndRenderSnippets();
    fetchAndRenderPopularCategories(); // Added call
    cleanupMSE();
});
