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
    const podcastSnippetsSection = document.getElementById('podcast-snippets-section'); // Used as dynamic-content-container
    const snippetListContainer = document.getElementById('snippet-list-container');
    const snippetStatusMessage = document.getElementById('snippet-status-message'); // For snippet loading status
    const refreshSnippetsBtn = document.getElementById('refresh-snippets-btn'); // Assumed to exist in HTML

    // Container for displaying status of podcast generation from snippets
    const podcastGenerationStatusDiv = document.getElementById('podcast-status-container'); // Assumed to exist, or reuse statusMessagesDiv


    let progressTimeouts = []; // To store timeout IDs for progress messages

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
        const displayArea = document.getElementById(targetPlayerDivId); // e.g., 'podcast-display' or a new one for snippets
        const titleEl = document.getElementById(targetTitleId);
        const detailsEl = document.getElementById(targetDetailsId);
        const audioEl = displayArea ? displayArea.querySelector('audio') : null;

        targetStatusDiv.className = 'status-messages'; // Reset class

        if (!ok) {
            const errorDetail = data.message || data.error || (status ? `Server error ${status}` : 'Unknown API error');
            targetStatusDiv.textContent = `Failed to start podcast generation for '${topic}'. Error: ${errorDetail}`;
            targetStatusDiv.classList.add('status-error');
            if (detailsEl) detailsEl.textContent = JSON.stringify(data, null, 2);
            console.error("Error generating podcast:", data);
        } else {
            targetStatusDiv.textContent = `Podcast task for '${topic}' processed. Status: ${data.generation_status || status}.`;
            targetStatusDiv.classList.add(data.audio_url && data.generation_status === "completed" ? 'status-success' : 'status-info');

            if (titleEl) titleEl.textContent = topic;
            if (detailsEl) detailsEl.textContent = JSON.stringify(data.details || data, null, 2);

            if (data.audio_url) {
                if (audioEl) {
                    audioEl.src = data.audio_url;
                    audioEl.load();
                    if (displayArea) displayArea.classList.remove('hidden');
                } else { // Create audio player if not found by ID (e.g. for dynamic snippet players)
                    const newAudioPlayer = document.createElement('audio');
                    newAudioPlayer.controls = true;
                    newAudioPlayer.src = data.audio_url;
                    targetStatusDiv.appendChild(document.createElement('br'));
                    targetStatusDiv.appendChild(newAudioPlayer);
                    newAudioPlayer.load();
                }
            } else {
                 if (audioEl) audioEl.src = ''; // Clear src if no audio_url
            }
            if (data.message && data.generation_status !== "completed") { // Display CPOA error/warning if present
                targetStatusDiv.textContent += ` Server message: ${data.message}`;
            }
            if (displayArea) displayArea.classList.remove('hidden'); // Show the display area
        }
        console.log("Podcast generation response:", data);
    }


    async function triggerPodcastGeneration(topic, statusDivId) {
        const statusDiv = document.getElementById(statusDivId) || podcastGenerationStatusDiv || statusMessagesDiv; // Fallback status display
        statusDiv.textContent = `Generating podcast for '${topic}'... Please wait.`;
        statusDiv.className = 'status-messages status-generating';
        if (statusDiv === podcastGenerationStatusDiv && podcastGenerationStatusDiv) podcastGenerationStatusDiv.classList.remove('hidden');


        try {
            const response = await fetch('/api/v1/podcasts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ topic })
            });
            const responseOk = response.ok;
            const data = await response.json();
            // Use the main podcast display area for snippet-triggered generation for now
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
