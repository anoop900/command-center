Task: tailor the candidate's base resume wording to one posting. Change as little as possible. Name, contact details,
education, employers, titles and dates are never changed.

1. Change only: "headline", "subhead", "profile" (the summary paragraph), the order and wording of "skills", and at
   most 2 bullet rewrites in "bullet_changes". Each bullet change quotes the exact current text in "old" and gives
   the new text in "new".
2. Every claim in your wording must be supported by PROFILE, judged by meaning: totals worked out from the dates and
   standard industry terms for work the PROFILE shows are fine. Do not make the scope bigger than the PROFILE shows
   for that employer (for example a national or enterprise reach, an industry, a seniority level, or a compliance
   name it does not give). Add a posting keyword only if the candidate truly has it.
3. "answers": short answers to any screening questions the posting asks, from PROFILE only. "why_company" is 80 to 150
   words on why this company, using only the posting and the PROFILE. Leave "answers" empty if there are none.
4. If a RULES block is given, none of your wording may contain a banned phrase or word it lists (reword the idea
   instead), and a "bullet_changes" entry should rewrite any base bullet that does.
5. If the posting wants something the candidate lacks, do not cover it up; leave it out.
6. If an ATS block is given, this is a second pass to help resume-scanning software match the posting. Put the exact
   posting title from it in "headline" or the first sentence of "profile" if the PROFILE supports that kind of role.
   Use the exact wording of each "add" phrase somewhere natural (skills, profile or a bullet change); each one is
   backed by the PROFILE. Prefer bullet changes that show a number the PROFILE gives (count, %, money, time).
   Never add anything from "gaps": the candidate does not have those.

Example:
```json
{"headline": "Cloud Architect | Azure | Automation", "subhead": "",
 "profile": "Cloud architect with ...",
 "skills": [{"label": "Cloud", "text": "Azure, networking, identity"}],
 "bullet_changes": [{"old": "Led the migration of ...", "new": "Led the Azure migration of ..."}],
 "answers": [{"question": "Why this role?", "answer": "..."}], "why_company": "..."}
```
