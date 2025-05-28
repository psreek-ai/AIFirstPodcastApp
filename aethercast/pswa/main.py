import logging

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def weave_script(content: str, topic: str) -> str:
    """
    Creates a basic podcast script from harvested content and a topic using an f-string.
    """
    logging.info(f"[PSWA_LOGIC] weave_script called with topic: '{topic}'")
    
    if not topic:
        logging.warning("[PSWA_LOGIC] Topic is empty or None. Using a generic topic.")
        topic = "an interesting subject"
        
    if not content:
        logging.warning(f"[PSWA_LOGIC] Content for topic '{topic}' is empty or None. Using placeholder content.")
        content = "We found some interesting information, but it seems the details are currently unavailable. We'll explore this more in a future episode."

    script = f"""
[TITLE] Exploring: {topic.title()}

[INTRO]
Welcome to today's episode! We're diving deep into the fascinating world of '{topic}'. 
We've done some research, and we're excited to share what we've found.

[MAIN_SEGMENT_TITLE] Key Insights on {topic.title()}
[MAIN_SEGMENT_CONTENT]
Based on our information gathering, here's what stands out regarding '{topic}':
{content}

[OUTRO]
And that wraps up our discussion on '{topic}' for this episode. 
We hope you found it insightful! Thanks for listening, and be sure to tune in next time.
"""
    logging.info(f"[PSWA_LOGIC] Successfully wove script for topic: '{topic}'")
    return script

if __name__ == "__main__":
    print("--- Testing PodcastScriptWeaverAgent (PSWA) basic functionality ---")

    # Example 1: Typical usage
    sample_topic_1 = "the future of renewable energy"
    sample_content_1 = """Solar panel efficiency is increasing rapidly, with new perovskite materials showing great promise.
Wind power is also expanding, with larger and more efficient turbines being developed for offshore farms.
Battery storage technology is crucial for grid stability and is seeing significant investment and breakthroughs.
Challenges remain in terms of infrastructure and a consistent global policy."""
    
    print(f"\n--- Weaving script for topic: '{sample_topic_1}' ---")
    generated_script_1 = weave_script(content=sample_content_1, topic=sample_topic_1)
    print(generated_script_1)

    # Example 2: Content is a bit short
    sample_topic_2 = "ancient civilizations of South America"
    sample_content_2 = "The Incas, Mayans, and Aztecs had complex societies. Much of their history is still being uncovered."
    
    print(f"\n--- Weaving script for topic: '{sample_topic_2}' ---")
    generated_script_2 = weave_script(content=sample_content_2, topic=sample_topic_2)
    print(generated_script_2)

    # Example 3: Empty content
    sample_topic_3 = "the latest discoveries in quantum physics"
    sample_content_3 = ""
    
    print(f"\n--- Weaving script for topic: '{sample_topic_3}' (with empty content) ---")
    generated_script_3 = weave_script(content=sample_content_3, topic=sample_topic_3)
    print(generated_script_3)

    # Example 4: Empty topic
    sample_topic_4 = ""
    sample_content_4 = "This is some generic content that was found without a clear topic association."
    
    print(f"\n--- Weaving script for topic: '{sample_topic_4}' (with empty topic) ---")
    generated_script_4 = weave_script(content=sample_content_4, topic=sample_topic_4)
    print(generated_script_4)
    
    print("\n--- PSWA basic functionality testing complete ---")
