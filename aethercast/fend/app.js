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
        updateStatus(`Initiating podcast generation for: "${topic}"...`, 'info');

        // Clear any previous timeouts just in case
        clearAllProgressTimeouts();

        // Simulated Progress Updates
        progressTimeouts.push(setTimeout(() => {
            updateStatus(`Gathering information for "${topic}"...`, 'generating');
        }, 2000)); // 2 seconds

        progressTimeouts.push(setTimeout(() => {
            updateStatus(`Crafting script with AI for "${topic}"... This may take a moment.`, 'generating');
        }, 5000)); // 5 seconds total

        progressTimeouts.push(setTimeout(() => {
            updateStatus(`Synthesizing audio for "${topic}"... Almost there!`, 'generating');
        }, 8000)); // 8 seconds total

        // API Call using fetch
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

            if (!ok) {
                // If response.ok was false, treat as an error from the start
                const errorDetail = data.message || data.error || (status ? `Server error ${status}` : 'Unknown API error');
                throw new Error(`API Error (${status}): ${errorDetail}`);
            }
            
            // Handle successful or partially successful responses (200 OK or 201 Created)
            // Based on API Gateway logic, 201 is for full success with audio.
            // 200 can be for "completed_with_warnings" or "completed_with_errors" where no audio is served.
            if (status === 201 && data.podcast_id && data.audio_url && data.generation_status === "completed") {
                updateStatus(`Podcast "${data.topic || topic}" is ready!`, 'success');
                podcastTopicTitle.textContent = data.topic || topic;
                audioPlayer.src = data.audio_url; 
                generationDetailsLog.textContent = JSON.stringify(data.details, null, 2);
                podcastDisplayDiv.classList.remove('hidden');
                audioPlayer.load(); 
            } else if (status === 200 && (data.generation_status === "completed_with_warnings" || data.generation_status === "completed_with_errors" || (data.generation_status === "completed" && !data.audio_url))) {
                const message = data.message || (data.details && data.details.error_message) || 'Podcast generation completed with issues, but no audio is available.';
                updateStatus(`Generation issue: ${message}`, 'error'); // Use 'error' class for visibility, even for warnings
                podcastTopicTitle.textContent = `Issue with: ${topic}`;
                generationDetailsLog.textContent = JSON.stringify(data.details || data, null, 2);
                podcastDisplayDiv.classList.remove('hidden');
            } else {
                // This case handles other 2xx responses that don't fit the above success/warning criteria,
                // or if a 201/200 response is missing expected fields.
                const message = data.message || (data.details && data.details.error_message) || 'Podcast generation finished with an unexpected status or missing data.';
                updateStatus(`Generation issue: ${message}`, 'error');
                podcastTopicTitle.textContent = `Issue with: ${topic}`;
                generationDetailsLog.textContent = JSON.stringify(data, null, 2);
                podcastDisplayDiv.classList.remove('hidden');
            }
        })
        .catch(error => {
            clearAllProgressTimeouts();
            const errorMessage = error.message || (error.error ? `${error.error}: ${error.message}` : 'Network error or API unreachable.');
            updateStatus(`API Request Failed: ${errorMessage}`, 'error');
            podcastTopicTitle.textContent = "API Request Failed";
            try {
                // Attempt to stringify if it's an object (like from a JSON error response)
                generationDetailsLog.textContent = (typeof error === 'object' && error !== null && Object.keys(error).length > 0) ? JSON.stringify(error, null, 2) : error.toString();
            } catch (e) { 
                generationDetailsLog.textContent = error.toString();
            }
            podcastDisplayDiv.classList.remove('hidden');
        })
        .finally(() => {
            generateBtn.disabled = false;
            clearAllProgressTimeouts(); 
        });
    });

    // Initial Status Message
    updateStatus("Please enter a topic and click 'Generate Podcast' to begin.", "info");
});
