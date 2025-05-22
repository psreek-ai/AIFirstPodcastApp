# Podcast Script Weaver Agent (PSWA)

The Podcast Script Weaver Agent (PSWA) is responsible for generating a full podcast script from harvested web content and a given topic/style.

## Key Responsibilities:

1.  **Input Processing:** Receives harvested content (a `HarvestedContentBundle` or equivalent structure from WCHA via CPOA), a podcast title suggestion, and desired style/persona from the Central Podcast Orchestrator Agent (CPOA).
2.  **Content Analysis & Synthesis (Conceptual):**
    *   (Future) Analyzes the provided text content from multiple sources.
    *   (Future) Synthesizes this information, identifies key points, and determines a narrative flow.
    *   **For current simulation:** It will primarily focus on using the provided content to inform a prompt for an LLM.
3.  **LLM Interaction (AIMS):**
    *   Formulates a detailed prompt for a Large Language Model (LLM) hosted by the AIMS service. The prompt will instruct the LLM to generate a podcast script, including:
        *   An engaging introduction.
        *   Several main segments discussing different aspects of the topic, drawing from the provided content.
        *   A concluding outro.
        *   (Optionally) Speaker cues or style notes if the persona requires it.
    *   Calls the AIMS (LLM) service with this prompt.
    *   Receives the generated script text from AIMS.
4.  **Script Structuring:**
    *   Parses the LLM-generated text.
    *   Structures the script into a `PodcastScript` object, which includes:
        *   `script_id` (unique identifier).
        *   `topic_id` (if available from input).
        *   `script_title_suggestion` (can be refined from input or LLM output).
        *   `full_text_script` (the complete generated script).
        *   `segments` (an array of objects, each representing a part like "Introduction", "Main Segment 1", "Outro", with their respective `script_content`).
        *   `estimated_reading_time_seconds`.
        *   `persona_used`.
5.  **Output:** Returns the structured `PodcastScript` object to the CPOA.

## Integration:

*   **Called by:** Central Podcast Orchestrator Agent (CPOA) via an API endpoint (e.g., `POST /weave_script`).
*   **Calls:** AIMS (LLM service) to generate the podcast script.
*   **Output:** A `PodcastScript` object.

This directory contains the source code and any specific configuration for the PSWA service.
It will be a Python-based service (e.g., using Flask) that interacts with the placeholder AIMS (LLM) service.Okay, I will continue developing the `PodcastScriptWeaverAgent` (PSWA).

I'll now create the `main.py` file for the PSWA. This initial version will include:
- Basic Flask setup.
- An endpoint `/weave_script` that CPOA can call.
- Logic to formulate a prompt for the AIMS (LLM) placeholder based on the input `retrieved_content`, `podcast_title`, and `podcast_style`.
- Functionality to call the AIMS (LLM) placeholder's API.
- Logic to parse the hardcoded LLM response and structure it into a `PodcastScript` object.
- Returning the `PodcastScript` object.
