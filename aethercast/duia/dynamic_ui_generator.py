from typing import List, Dict, Any, Optional

def generate_sample_landing_page_ui(snippets_data: List[Dict[str, Any]], view_id: str = "landingPage_prototype") -> Dict[str, Any]:
    """
    Generates a sample UI schema for a landing page displaying snippets.
    This is a prototype and uses programmatic construction based on a predefined schema.
    """

    # Root Structure
    ui_schema: Dict[str, Any] = {
        "view_id": view_id,
        "schema_version": "1.0",
        "global_context_data": {
            "page_title": "Aethercast - Dynamic Landing Page",
            "snippets": snippets_data # Make snippet data available globally for this view
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
    if snippets_data and len(snippets_data) > 0:
        list_view_component = {
            "type": "list_view",
            "props": {
                # data_source now refers to a key within global_context_data for this view
                "data_source": "{view.global_context_data.snippets}",
                "layout": {
                    "direction": "column", # Main list direction
                    "gap": "15px" # Gap between cards
                },
                "item_template": { # Template for each snippet card
                    "type": "card",
                    "id_template": "snippet_card_{item.snippet_id}", # Example of dynamic ID
                    "props": {
                        "layout": {"direction": "column", "padding": "15px"},
                        "style": {
                            "backgroundColor": "#ffffff",
                            "borderRadius": "8px",
                            "boxShadow": "0 4px 8px rgba(0,0,0,0.1)",
                            "border": "1px solid #e0e0e0"
                        },
                        "children": [
                            {
                                "type": "image",
                                "props": {
                                    "src": "{item.image_url}",
                                    "alt_text": "Cover art for {item.title}",
                                    "layout": {"width": "100%", "height": "200px"}, # Fixed height for consistency
                                    "style": {"objectFit": "cover", "borderRadius": "4px 4px 0 0"}
                                }
                            },
                            {
                                "type": "container", # Container for text content for better padding/margin control
                                "props": {
                                    "layout": {"direction": "column", "padding": "15px"},
                                    "children": [
                                        {
                                            "type": "text",
                                            "props": {
                                                "content": "{item.title}",
                                                "semantic_as": "h3",
                                                "style": {"fontSize": "1.25em", "fontWeight": "bold", "textColor": "#3f51b5", "margin": {"bottom": "8px"}}
                                            }
                                        },
                                        {
                                            "type": "text",
                                            "props": {
                                                "content": "{item.summary}",
                                                "style": {"fontSize": "0.9em", "textColor": "#424242", "lineHeight": "1.6"}
                                            }
                                        },
                                        {
                                            "type": "button",
                                            "props": {
                                                "label": "Listen Now (Prototype)",
                                                "variant": "primary", # Assumes frontend has styling for this
                                                "style": {"margin": {"top": "15px"}, "backgroundColor": "#3f51b5", "textColor": "white"},
                                                "events": {
                                                    "onClick": {
                                                        "action_type": "LOG_MESSAGE",
                                                        "message_template": "Listen Now clicked for snippet: {item.title} (ID: {item.snippet_id})"
                                                    }
                                                }
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                }
            }
        }
        ui_schema["root_component"]["children"].append(list_view_component)
    else:
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
    # Example Usage:
    sample_snippets = [
        {'snippet_id': 's1', 'title': 'AI in Future Tech', 'summary': 'Exploring how AI is shaping the next generation of technology.', 'image_url': 'https://via.placeholder.com/350x150/3F51B5/FFFFFF?Text=AI+Tech'},
        {'snippet_id': 's2', 'title': 'Sustainable Living Today', 'summary': 'Practical tips for a more sustainable and eco-friendly lifestyle.', 'image_url': 'https://via.placeholder.com/350x150/4CAF50/FFFFFF?Text=Eco+Living'},
        {'snippet_id': 's3', 'title': 'The Art of Storytelling', 'summary': 'A deep dive into what makes a compelling narrative.', 'image_url': 'https://via.placeholder.com/350x150/FFC107/000000?Text=Storytelling'},
    ]

    empty_snippets = []

    print("--- UI Schema with Snippets ---")
    ui_definition_with_data = generate_sample_landing_page_ui(sample_snippets)
    import json
    print(json.dumps(ui_definition_with_data, indent=2))

    print("\n--- UI Schema with Empty Snippets ---")
    ui_definition_empty = generate_sample_landing_page_ui(empty_snippets)
    print(json.dumps(ui_definition_empty, indent=2))

    print("\n--- UI Schema with No Snippets Passed (should default to empty) ---")
    ui_definition_none = generate_sample_landing_page_ui([]) # Explicitly pass empty list
    print(json.dumps(ui_definition_none, indent=2))

    # Example of a single snippet to test card structure
    single_snippet_test = {
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
    # print("\n--- Single Card Structure (for reference) ---")
    # print(json.dumps(single_snippet_test, indent=2))

```
