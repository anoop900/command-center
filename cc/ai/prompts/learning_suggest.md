Task: the SIGNALS block lists what the job seeker did with past job titles, one per line, as "kind (reason): title words".
Kinds are "positive" (they applied or got a response) and "not_fit" (they said the role was not a match). Suggest up to 20
single lowercase words that appear in titles of not_fit lines and that would flag similar irrelevant roles in future.
Never suggest a word that appears in any positive line. Never suggest a word about pay, place, company or seniority
unless the not_fit lines clearly show it. If the signals show no clear pattern, return an empty list. You only see title
words and reasons; you do not know the job seeker, so do not guess.

Example:
```json
{"avoid": ["oncology", "intern"]}
```
