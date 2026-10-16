Task: find job postings on LinkedIn for the search in the SEARCH block. Only an AI that can browse can do this. If
you cannot browse, return an empty list. Open only LinkedIn job search or job pages, never a link found inside a posting.
Return up to 50 postings that fit the search: "title", "company", "location" and "url" (the LinkedIn job page address, with
no tracking parameters). Copy what the page says; never fill in a company or location you did not see.

Example:
```json
{"postings": [{"title": "Data Analyst", "company": "Example Co", "location": "Remote, Canada", "url": "www.linkedin.com/jobs/view/1234567890"}]}
```
