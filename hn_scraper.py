import requests
from bs4 import BeautifulSoup

HN_URL = "https://news.ycombinator.com"


def fetch_hn_stories():
    try:
        response = requests.get(HN_URL, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        stories = []
        story_rows = soup.select("tr.athing")[:20]

        for row in story_rows:
            story_id = row.get("id")
            title_link = row.select_one("span.titleline a")

            if not title_link:
                continue

            title = title_link.text.strip()
            url = title_link.get("href", "")
            hn_link = f"https://news.ycombinator.com/item?id={story_id}"

            subtext = row.find_next_sibling("tr").select_one("td.subtext")
            age = subtext.select_one("span.age").text if subtext else "unknown"

            stories.append(
                {
                    "id": story_id,
                    "title": title,
                    "url": url,
                    "hn_link": hn_link,
                    "age": age,
                }
            )

        return stories
    except Exception:
        return []


async def debug_hn_scraping():
    debug_info = {"status": "starting", "steps": {}, "errors": [], "sample_stories": []}

    try:
        debug_info["steps"]["network"] = "testing"
        try:
            response = requests.get(HN_URL, timeout=10)
            response.raise_for_status()
            debug_info["steps"]["network"] = (
                f"success ({len(response.content)} bytes received)"
            )
            debug_info["steps"]["status_code"] = response.status_code
        except Exception as e:
            debug_info["steps"]["network"] = f"failed: {str(e)}"
            debug_info["errors"].append(f"Network error: {str(e)}")
            return debug_info

        debug_info["steps"]["parsing"] = "testing"
        try:
            soup = BeautifulSoup(response.text, "html.parser")
            debug_info["steps"]["parsing"] = "success"
        except Exception as e:
            debug_info["steps"]["parsing"] = f"failed: {str(e)}"
            debug_info["errors"].append(f"HTML parsing error: {str(e)}")
            return debug_info

        debug_info["steps"]["selectors"] = {}
        story_rows = soup.select("tr.athing")
        debug_info["steps"]["selectors"]["tr.athing"] = f"found {len(story_rows)} rows"

        alt_story_rows = soup.select("tr.athing.submission")
        debug_info["steps"]["selectors"]["tr.athing.submission"] = (
            f"found {len(alt_story_rows)} rows"
        )

        if len(story_rows) == 0:
            debug_info["errors"].append("No story rows found with main selector")
            if len(alt_story_rows) > 0:
                story_rows = alt_story_rows
                debug_info["steps"]["analysis"] = (
                    "Using alternative selector (tr.athing.submission)"
                )

        debug_info["steps"]["parsing_analysis"] = {
            "total_rows": len(story_rows),
            "limited_to": min(20, len(story_rows)),
        }

        parsed_count = 0
        failed_count = 0
        failure_reasons = {}

        for i, row in enumerate(story_rows[:5]):
            story_debug = {"index": i, "steps": {}}

            story_id = row.get("id")
            story_debug["steps"]["id"] = story_id if story_id else "MISSING"

            title_link = row.select_one("span.titleline a")
            if title_link:
                story_debug["steps"]["title_link"] = "found"
                story_debug["title"] = (
                    title_link.text.strip()[:50] + "..."
                    if len(title_link.text.strip()) > 50
                    else title_link.text.strip()
                )
                story_debug["url"] = (
                    title_link.get("href", "")[:50] + "..."
                    if len(title_link.get("href", "")) > 50
                    else title_link.get("href", "")
                )
            else:
                story_debug["steps"]["title_link"] = "MISSING"
                failure_reasons["title_link_missing"] = (
                    failure_reasons.get("title_link_missing", 0) + 1
                )
                failed_count += 1
                debug_info["sample_stories"].append(story_debug)
                continue

            subtext = row.find_next_sibling("tr").select_one("td.subtext")
            if subtext:
                age_element = subtext.select_one("span.age")
                if age_element:
                    story_debug["steps"]["age"] = age_element.text
                else:
                    story_debug["steps"]["age"] = "MISSING"
                    failure_reasons["age_missing"] = (
                        failure_reasons.get("age_missing", 0) + 1
                    )
            else:
                story_debug["steps"]["subtext"] = "MISSING"
                failure_reasons["subtext_missing"] = (
                    failure_reasons.get("subtext_missing", 0) + 1
                )

            debug_info["sample_stories"].append(story_debug)

            if title_link:
                parsed_count += 1

        debug_info["steps"]["parsing_results"] = {
            "parsed_count": parsed_count,
            "failed_count": failed_count,
            "failure_reasons": failure_reasons,
        }

        final_stories = fetch_hn_stories()
        debug_info["steps"]["final_result"] = (
            f"fetch_hn_stories() returned {len(final_stories)} stories"
        )

        debug_info["status"] = "completed"
        return debug_info

    except Exception as e:
        debug_info["status"] = f"failed: {str(e)}"
        debug_info["errors"].append(f"General error: {str(e)}")
        return debug_info
