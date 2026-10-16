Task: list the jobs and skills in the RESUME block. Copy text exactly as written there. Do not reword, add, merge or
infer anything. Every employer, title, fact and skill must appear word for word in the resume.

- "jobs": newest first. Each has "employer", "title", "dates" (as written, or an empty string) and "facts": up to 10
  lines copied from that job's bullet points.
- "skills": up to 30 skill or tool names that appear in the resume.

Example:
```json
{"jobs": [{"employer": "Example Corp", "title": "Cloud Architect", "dates": "2019 - 2023", "facts": ["Led a team of 6 engineers"]}], "skills": ["Azure", "Terraform"]}
```
