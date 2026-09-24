# Identification study — item {item}

Below is an excerpt from a U.S. SEC filing ({form}, filed {filed}), mostly from the
Management's Discussion and Analysis. The company's name, ticker, web addresses and
tax ID were replaced with [ISSUER] or removed; ordinary words that happened to share
a word with the company's name may also have been replaced.

Using ONLY your own background knowledge, identify the company. Do not search the web
or use any tool except writing your answer file. If you have no real idea, say so
with a low confidence rather than guessing a famous name.

Write a JSON object with exactly these keys to answers/{item}.json:
- "item": "{item}"
- "company_guess": your best guess of the company's name, or null
- "ticker_guess": its ticker at the time, or null
- "alternates": a list of up to 2 other candidate company names (may be empty)
- "confidence": probability 0-1 that company_guess is correct
- "industry": the company's industry in at most 6 words
- "clue": at most 20 words on what identified it, or "nothing specific"

Then reply with just the word DONE.

---

<10,000 characters of strictly scrubbed MD&A>
