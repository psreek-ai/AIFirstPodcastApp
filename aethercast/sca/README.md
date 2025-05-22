# Snippet Craft Agent (SCA)

The Snippet Craft Agent (SCA) is responsible for generating short, engaging podcast snippets based on a given topic.

## Key Responsibilities:

1.  **Input Processing:** Receives a topic (e.g., a `TopicObject` or topic ID and brief) from the Central Podcast Orchestrator Agent (CPOA).
2.  **LLM Interaction (AIMS):**
    *   Formulates a suitable prompt for a Large Language Model (LLM) hosted by the AIMS service. The prompt will guide the LLM to generate a concise title and script/text for the snippet.
    *   Calls the AIMS (LLM) service with this prompt.
    *   Receives the generated text (title and snippet content) from AIMS.
3.  **TTS Interaction (AIMS_TTS - Conceptual for this stage):**
    *   (Future Responsibility) Formulates a request for the AIMS_TTS service to synthesize audio for the generated snippet text.
    *   (Future Responsibility) Receives the audio URL or audio data from AIMS_TTS.
    *   For the current development stage, the `audio_url` will be a placeholder string, as CPOA's `call_snippet_craft_agent` currently returns a hardcoded one. The focus is on LLM integration.
4.  **Snippet Structuring:** Assembles the generated title, text, and other relevant information (like `topic_id`, `snippet_id`) into a `SnippetDataObject`.
5.  **Output:** Returns the `SnippetDataObject` to the CPOA.

## Integration:

*   **Called by:** Central Podcast Orchestrator Agent (CPOA) via an API endpoint (e.g., `POST /craft_snippet`).
*   **Calls:**
    *   AIMS (LLM service) to generate snippet text.
    *   (Conceptually) AIMS_TTS service to generate snippet audio.
*   **Output:** A `SnippetDataObject` containing the generated snippet's metadata and content.

This directory contains the source code and any specific configuration for the SCA service.
It will be a Python-based service (e.g., using Flask) that interacts with the placeholder AIMS services.
