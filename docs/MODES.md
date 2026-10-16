# Modes: how much AI to use

Command Center does every non-AI step itself (reading alert and status emails, de-duplicating, gates on pay,
location and work authorization, reminders, the tracker). AI is an optional worker for judgement steps only. You pick
a mode in the setup wizard and can change it any time from **Setup**; your data stays as it is when you switch.

| What | No AI | Chat | Assisted | Autopilot |
|---|---|---|---|---|
| Alerts and status emails read, de-duplicated, tracked | auto | auto | auto | auto |
| Interview invites (calendar) | yes | yes | yes | yes |
| Company job-board feeds | yes | yes | yes | yes |
| Pay / location / work-authorization gates | yes | yes | yes | yes |
| Reminders, people, tasks, offers | yes | yes | yes | yes |
| Fit scoring | keyword score only | paste batch | when started | auto |
| Tailored resume and answers | base resume | paste per kit | on request | auto (top roles) |
| Claim trace and independent review | no | paste | yes | yes |
| Interview prep from stories | no | paste | on request | auto |
| Cover letter (one page, only when you press the button) | no | paste | on request | on request |
| Daily improvement coaching (wording and praise) | plain wording | paste | on request | auto |
| LinkedIn reading beyond alert emails | no | no | same as Autopilot | browser-capable AI only |
| Active learning: an AI suggests title terms to avoid (optional, off until you turn it on) | no | no | if turned on | if turned on |
| Form-filler browser extension | yes | yes | yes | yes |
| Phone dashboard | later | later | later | later |

The same table is in code (`cc/modes.py`) and drives the wizard and the dashboard's Mode panel, so this page and the
app cannot disagree.

## No AI (default)
Free. Nothing leaves your computer. Scoring is keyword overlap and is labelled that way. Kits use your base resume.
Good for trying the app, or if you do not want any AI near your job search.

## Chat
Uses a chatbot subscription you already pay for. The app prepares one prompt per batch; you paste it into any chatbot
and paste the answer back. The app checks the answer against the task's schema before using it. You decide exactly
what the chatbot sees.

## Assisted
Uses your own AI account, only when you press a button or run a command. Posting and profile text go to your provider
when you start a step. Pick the provider in the wizard: the Claude or Codex command-line app you have installed,
the Anthropic, OpenAI or Gemini API, OpenRouter, any OpenAI-compatible service, or Ollama (runs on your computer, no
key). The wizard's **Test it** button makes one real call.

## Autopilot
The same AI, on every scheduled run, capped by the call budget in `config.toml` (the dashboard shows usage). Posting
and profile text go to your provider on every run. Pick a provider you trust with that text.

## Things to know before choosing
- **Cost.** API providers bill per call; free tiers may use your inputs for training. The call budget limits spend
  per run and per day but is not a billing guarantee.
- **Privacy.** Only Assisted and Autopilot send anything to a provider. Ollama keeps it all local.
- **Quality varies by model.** Small local models parse and score less reliably than hosted ones. Every AI output is
  checked against a schema, quoted facts are checked against your resume, and nothing is used until you confirm it.
- **What AI never does.** It never submits an application, sends a message, logs in for you or invents a fact. Your
  profile is the only source of facts, and postings and emails are treated as untrusted data, never instructions.
