from typing import List, Dict, Any, Optional
import logging # Added for logging

# --- Logger Setup ---
logger = logging.getLogger(__name__)
# Basic config for the logger if no handlers are configured by the calling service
# This allows the module to log even if the calling application (e.g. CPOA) hasn't configured root logging.
if not logger.hasHandlers():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - DUIA - %(levelname)s - %(message)s')


def generate_sample_landing_page_ui(snippets_data: Optional[List[Dict[str, Any]]], view_id: str = "landingPage_prototype") -> Dict[str, Any]:
    """
    Generates a sample UI schema for a landing page displaying snippets.
    This version includes robust error handling for snippet data.
    """
    # Initial check for snippets_data type and content
    if not isinstance(snippets_data, list):
        logger.error(f"generate_sample_landing_page_ui called with invalid snippets_data type: {type(snippets_data)}. Defaulting to empty list.")
        snippets_data = [] # Process as empty to avoid further errors

    # Root Structure
    ui_schema: Dict[str, Any] = {
        "view_id": view_id,
        "schema_version": "1.0", # Consider using a global constant for schema version
        "global_context_data": {
            "page_title": "Aethercast - Dynamic Landing Page",
            # We'll populate snippets into the list_view component directly after validation,
            # rather than putting potentially problematic data into global_context_data first.
            # "snippets": snippets_data
        },
        "root_component": {
            "type": "container",
            "props": {
                "layout": {"direction": "column", "gap": "20px", "padding": "15px"},
                "style": {"backgroundColor": "#f0f2f5", "minHeight": "100vh"}
            },
            "children": []
        }
    }

    # Header Component
    header_component = {
        "type": "text",
        "props": {
            "content": "{view.global_context_data.page_title}", # Data binding
            "semantic_as": "h1",
            "style": {"textAlign": "center", "textColor": "#1a237e", "margin": {"bottom": "20px"}}
        }
    }
    ui_schema["root_component"]["children"].append(header_component)

    # Snippet List Component
    processed_snippet_items_for_list_view = [] # This will hold validated and processed items for the list_view

    if snippets_data: # Already ensured snippets_data is a list or empty list
        for i, item_data in enumerate(snippets_data):
            if not isinstance(item_data, dict):
                logger.warning(f"Snippet item at index {i} is not a dictionary. Skipping. Item: {item_data}")
                continue

            snippet_id = item_data.get('snippet_id')
            if not snippet_id: # Critical field: if missing, skip this snippet
                logger.error(f"Snippet item at index {i} missing critical 'snippet_id'. Skipping this snippet. Item data (first 100 chars): {str(item_data)[:100]}")
                continue

            # Ensure snippet_id is a string for template compatibility
            snippet_id_str = str(snippet_id)

            title = item_data.get('title')
            if not title:
                logger.warning(f"Snippet item (ID: {snippet_id_str}) missing 'title'. Using default 'Untitled Snippet'.")
                title = "Untitled Snippet"

            summary = item_data.get('summary')
            if not summary:
                logger.info(f"Snippet item (ID: {snippet_id_str}, Title: '{title}') missing 'summary'. Using default.")
                summary = "No summary available."

            # Handle image_url: prefer 'image_url_signed', then 'image_url', then a default placeholder
            image_url = item_data.get('image_url_signed', item_data.get('image_url'))
            if not image_url:
                logger.info(f"Snippet item (ID: {snippet_id_str}, Title: '{title}') missing 'image_url' or 'image_url_signed'. Using placeholder.")
                image_url = "static/images/placeholder_image_300x200.png" # Default placeholder image

            # Add the processed item for the list_view data_source.
            # The item_template will then use these fields.
            processed_snippet_items_for_list_view.append({
                "snippet_id": snippet_id_str,
                "title": str(title), # Ensure string type
                "summary": str(summary), # Ensure string type
                "image_url": str(image_url) # Ensure string type
                # Other fields like source_name can be added if needed by the template
            })

    # Update global_context_data with the processed (safe) snippets
    ui_schema["global_context_data"]["snippets"] = processed_snippet_items_for_list_view

    if processed_snippet_items_for_list_view: # Only add list_view if there are items to show
        list_view_component = {
            "type": "list_view",
            "props": {
                "data_source": "{view.global_context_data.snippets}", # Points to the processed list
                "layout": {"direction": "column", "gap": "15px"},
                "item_template": {
                    "type": "card",
                    "id_template": "snippet_card_{item.snippet_id}",
                    "props": {
                        "layout": {"direction": "column", "padding": "15px"},
                        "style": {
                            "backgroundColor": "#ffffff", "borderRadius": "8px",
                            "boxShadow": "0 4px 8px rgba(0,0,0,0.1)", "border": "1px solid #e0e0e0"
                        },
                        "children": [
                            {
                                "type": "image",
                                "props": {
                                    "src": "{item.image_url}", "alt_text": "Cover art for {item.title}",
                                    "layout": {"width": "100%", "height": "200px"},
                                    "style": {"objectFit": "cover", "borderRadius": "4px 4px 0 0"}
                                }
                            },
                            {
                                "type": "container",
                                "props": {
                                    "layout": {"direction": "column", "padding": "15px"},
                                    "children": [
                                        {"type": "text", "props": {"content": "{item.title}", "semantic_as": "h3", "style": {"fontSize": "1.25em", "fontWeight": "bold", "textColor": "#3f51b5", "margin": {"bottom": "8px"}}}},
                                        {"type": "text", "props": {"content": "{item.summary}", "style": {"fontSize": "0.9em", "textColor": "#424242", "lineHeight": "1.6"}}},
                                        {"type": "button", "props": {
                                            "label": "Listen Now (Prototype)", "variant": "primary",
                                            "style": {"margin": {"top": "15px"}, "backgroundColor": "#3f51b5", "textColor": "white"},
                                            "events": {"onClick": {"action_type": "LOG_MESSAGE", "message_template": "Listen Now clicked for snippet: {item.title} (ID: {item.snippet_id})"}}
                                        }}
                                    ]
                                }
                            }
                        ]
                    }
                }
            }
        }
        ui_schema["root_component"]["children"].append(list_view_component)
    else: # No valid snippets to display
        logger.info("No valid snippets to display after processing. Showing 'no snippets' message.")
        no_snippets_component = {
            "type": "text",
            "props": {
                "content": "No snippets available at the moment. Please check back later!",
                "semantic_as": "p",
                "style": {"textAlign": "center", "textColor": "#555", "fontSize": "1.1em", "padding": "20px"}
            }
        }
        ui_schema["root_component"]["children"].append(no_snippets_component)

    return ui_schema

if __name__ == '__main__':
    # Configure basic logging for __main__ to see output from the logger
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Example Usage:
    sample_snippets_valid = [
        {'snippet_id': 's1', 'title': 'AI in Future Tech', 'summary': 'Exploring how AI is shaping the next generation of technology.', 'image_url': 'https://via.placeholder.com/350x150/3F51B5/FFFFFF?Text=AI+Tech'},
        {'snippet_id': 's2', 'title': 'Sustainable Living Today', 'summary': 'Practical tips for a more sustainable and eco-friendly lifestyle.', 'image_url_signed': 'https://via.placeholder.com/350x150/4CAF50/FFFFFF?Text=Eco+Living+Signed'}, # Test signed URL
        {'snippet_id': 's3', 'title': 'The Art of Storytelling', 'summary': 'A deep dive into what makes a compelling narrative.', 'image_url': 'https://via.placeholder.com/350x150/FFC107/000000?Text=Storytelling'},
    ]

    snippets_with_missing_data = [
        {'snippet_id': 'm1', 'summary': 'Only summary here.'}, # Missing title, image_url
        {'title': 'Only Title Here', 'summary': 'This one has a title but no ID.'}, # Missing snippet_id (will be skipped)
        {'snippet_id': 'm2', 'title': 'Valid with ID and Title', 'image_url': None}, # Missing image_url, summary
        None, # Invalid item, not a dict
        "Just a string item", # Invalid item
        {'snippet_id': 'm3', 'title': 'Another Valid One'} # Missing summary, image_url
    ]

    empty_snippets = []

    print("--- UI Schema with Valid Snippets ---")
    ui_definition_valid = generate_sample_landing_page_ui(sample_snippets_valid)
    print(json.dumps(ui_definition_valid, indent=2))

    print("\n--- UI Schema with Missing/Invalid Data (expect warnings/errors in logs) ---")
    ui_definition_missing = generate_sample_landing_page_ui(snippets_with_missing_data)
    print(json.dumps(ui_definition_missing, indent=2))

    print("\n--- UI Schema with Empty Snippets List ---")
    ui_definition_empty = generate_sample_landing_page_ui(empty_snippets)
    print(json.dumps(ui_definition_empty, indent=2))

    print("\n--- UI Schema with snippets_data as None ---")
    ui_definition_none = generate_sample_landing_page_ui(None)
    print(json.dumps(ui_definition_none, indent=2))

    # Example of a single snippet card structure for reference (not a full UI schema)
    # This is useful for understanding the item_template's output for one item.
    single_snippet_card_template_example = {
        "type": "card",
        "id_template": "snippet_card_{item.snippet_id}",
        "props": {
            "layout": {"direction": "column", "padding": "15px"},
            "style": {"backgroundColor": "#ffffff", "borderRadius": "8px", "boxShadow": "0 4px 8px rgba(0,0,0,0.1)"},
            "children": [
                {"type": "image", "props": {"src": "{item.image_url}", "alt_text": "{item.title}", "style": {"width": "100%", "aspect_ratio": "16/9"}}},
                {"type": "text", "props": {"content": "{item.title}", "semantic_as": "h3"}},
                {"type": "text", "props": {"content": "{item.summary}"}},
                {"type": "button", "props": {"label": "Listen Now", "events": {"onClick": {"action_type": "LOG_MESSAGE", "message_template": "Clicked {item.title}"}}}}
            ]
        }
    }
    # This part is just for local testing of the card structure, not a full UI schema
    # print("\n--- Single Card Structure (for reference based on template) ---")
    # print(json.dumps(single_snippet_card_template_example, indent=2))

```
