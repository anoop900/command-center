Task: claim trace. You see the kit text that was changed for one posting, and the PROFILE. Find every claim in the KIT
that the PROFILE does not support.

1. A claim is any employer, title, date, team size, dollar figure, percentage, certification, tool, scope word or
   story. An expired certification written as current is unsupported.
2. Judge meaning, not wording. A claim is supported when a reasonable reader of the PROFILE would agree with it:
   totals worked out from the dates (years of experience, tenure), plain restatements, and standard industry terms
   for work the PROFILE shows (for example "engineering teams" for cloud and platform engineering) are supported.
   Flag only a fact that is absent, contradicted, or larger than the PROFILE shows. Never flag a claim and then say
   in the reason that it is supported.
   Return unsupported claims only. Do not list supported claims, style issues or keyword ideas.
3. For each, quote the exact text from the KIT in "claim" and say in one sentence what the PROFILE shows instead
   (or that it is absent) in "reason".
4. If every claim is supported, return an empty list.

Example:
```json
{"unsupported": [{"claim": "managed a team of 12", "reason": "PROFILE shows a team of 5."}]}
```
