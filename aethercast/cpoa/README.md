# Central Podcast Orchestrator Agent (CPOA)

The CPOA is the brain of the Aethercast system. It is responsible for:

1.  **Receiving and Interpreting Requests:** Handling incoming HTTP requests from the API Gateway, primarily for generating podcast snippets and full podcasts.
2.  **Workflow Management:** Orchestrating the complex workflows involving multiple specialized AI agents to fulfill user requests. This includes:
    *   **Snippet Generation Workflow:** Coordinating `TopicDiscoveryAgent` and `SnippetCraftAgent`.
    *   **Full Podcast Generation Workflow:** Coordinating `WebContentHarvesterAgent`, `PodcastScriptWeaverAgent`, and `VoiceForgeAgent`.
3.  **State Management:** Tracking the status of active generation tasks. Initially, this will be in-memory, with plans to integrate with persistent Data Stores.
4.  **Agent Communication:** Defining the interfaces and protocols for communicating with downstream specialized agents.
5.  **Error Handling and Retry Logic:** Managing failures in the workflow and implementing retry mechanisms where appropriate.

The CPOA acts as a central controller, ensuring that all parts of the podcast generation process are executed in the correct order and that the final product meets the user's requirements.

This directory contains the source code and any specific configuration for the CPOA service.
For now, it will be a simple Python-based service (e.g., using Flask or FastAPI) that simulates these responsibilities.
