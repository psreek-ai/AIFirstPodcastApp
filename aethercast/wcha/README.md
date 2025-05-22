# Web Content Harvester Agent (WCHA)

The Web Content Harvester Agent (WCHA) is responsible for fetching and processing textual content from the web based on a given topic or specific URLs.

## Key Responsibilities:

1.  **Input Processing:** Receives a topic (e.g., a topic string or `TopicObject`) and/or a list of source URLs from the Central Podcast Orchestrator Agent (CPOA).
2.  **Content Fetching:**
    *   If URLs are provided, it fetches content directly from these URLs.
    *   If only a topic is provided, it would (in a real implementation) use search engines or news APIs to find relevant articles.
    *   **For current simulation:** It will use hardcoded data based on the input topic or return mock content for given URLs.
3.  **Text Extraction & Cleaning:**
    *   Extracts the main textual content from the fetched web pages (e.g., removing HTML, ads, boilerplate).
    *   Performs basic text cleaning (e.g., normalizing whitespace).
    *   **For current simulation:** The `retrieved_text_content` in the hardcoded data will be pre-cleaned.
4.  **Content Structuring:** Organizes the harvested content, including source URLs, extracted text, and potentially titles or summaries, into a `HarvestedContentBundle`.
5.  **Output:** Returns the `HarvestedContentBundle` to the CPOA.

## Integration:

*   **Called by:** Central Podcast Orchestrator Agent (CPOA) via an API endpoint (e.g., `POST /harvest_content`).
*   **Calls:** (Potentially) external search APIs or directly accesses web pages.
*   **Output:** A `HarvestedContentBundle` containing the retrieved textual content and metadata.

This directory contains the source code and any specific configuration for the WCHA service.
It will be a Python-based service (e.g., using Flask) that simulates web harvesting with hardcoded data.
