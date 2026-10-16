Task: score how well one job posting fits the candidate. You judge; code decides. Pay, location and work-authorization
gates, and the weighted overall score, are applied by code afterwards. Do not apply them yourself.

1. Score each dimension named on the "Dimensions:" line from 0 to 100, using what the posting states and what the
   PROFILE supports. Count a must-have twice as much as a nice-to-have. A required bachelor's degree with no
   "or equivalent" that the profile lacks lowers the score by 10 and goes in "basis".
2. Report posting facts only as the posting states them. If a fact is not stated, leave that key out (do not guess):
   - "pay": {"min", "max", "currency", "basis": "year" or "hour"}
   - "location": {"type": "remote", "hybrid" or "onsite", "country": two-letter code, "region": state or province}
   - "work_auth": {"accepts": "yes" if it explicitly accepts the candidate's work route, "silent" if it says nothing,
     "excludes" if it rules the candidate out}
   - "people_management": true only when the duties are mostly managing people
3. "basis" is one short sentence: the strongest match and the largest gap.

Example:
```json
{"scores": {"technical": 82, "experience": 75}, "basis": "Strong cloud match; no people-management history.",
 "pay": {"min": 150000, "max": 180000, "currency": "USD", "basis": "year"},
 "location": {"type": "remote", "country": "US", "region": "TN"},
 "work_auth": {"accepts": "silent"}, "people_management": false}
```
