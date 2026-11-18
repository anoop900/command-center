Task: read the RESUME and list the job fields this person should be shown roles in, the way a job site builds a feed
from an uploaded resume. Output words that appear in job TITLES of those roles (for example "cloud", "infrastructure",
"nursing", "payroll", "devops", "forward deployed"), lower case, one to three words each, 8 to 20 items, most
central first. Include the field names, the common title nouns for the work and the main tools or specialities only when
they show up in titles. Do not include level words (senior, lead, director, manager), company names, places or generic
words such as "business", "services" or "solutions".

"senior_ic" is the title nouns of individual-contributor roles in this field that are worth showing at staff or principal
level (for example "engineer", "architect"); leave it empty when the field has none.

Example:
```json
{"areas": ["cloud", "infrastructure", "platform", "devops", "sre", "architecture"], "senior_ic": ["engineer", "architect"]}
```
