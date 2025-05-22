# NoSQL Data Store: TopicObjects Collection

## Purpose
Stores information about various topics that can be used to generate podcast snippets and full podcasts.

## Schema
```json
{
  "topic_id": "string", // Unique identifier for the topic (e.g., "tech_ai_ethics")
  "display_name": "string", // User-friendly name (e.g., "AI Ethics in Technology")
  "description": "string", // A brief description of the topic
  "keywords": ["string"], // Array of keywords for searching and filtering
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

## Indexes
- `topic_id` (Primary Key)
- `keywords` (For efficient searching)
- `display_name` (For sorting and display)

## Example
```json
{
  "topic_id": "science_space_exploration",
  "display_name": "Space Exploration",
  "description": "Covers topics related to the exploration of outer space, including missions, discoveries, and future prospects.",
  "keywords": ["space", "nasa", "mars", "moon", "rockets"],
  "created_at": "2023-10-26T10:00:00Z",
  "updated_at": "2023-10-26T10:00:00Z"
}
```
