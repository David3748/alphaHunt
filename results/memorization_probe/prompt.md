# Memorization study — batch <n>

These are excerpts from U.S. SEC filings (10-K / 10-Q, usually the MD&A section).
The company's name and ticker were replaced with [ISSUER]; some ordinary words
containing name fragments were also replaced. The filing date is given.

Answer ONLY from your own background knowledge. Do NOT search the web or use any
tool except writing your answer file. It is fine, and useful, to say you do not
recognise a company. Be honest about uncertainty; guessing a famous company when
you have no real signal hurts the study.

For EVERY item, produce an object with:
- "item": the item id
- "company_guess": best guess of the company name, or null
- "ticker_guess": best guess of the ticker, or null
- "id_confidence": probability (0-1) that your company guess is correct
- "recall": what you REMEMBER about the stock's performance over the ~90 days after
  the filing date, relative to the S&P 500: one of "strong_outperform" (beat by
  >= 20 percentage points), "outperform", "underperform", "strong_underperform"
  (trailed by >= 20 points), or "no_memory"
- "p_beat20": your probability (0-100) that the stock beat the S&P 500 by >= 20
  percentage points over the 90 calendar days after the filing date
- "clue": <= 15 words on what (if anything) identified the company

Write a JSON array of all 8 objects to: answers_<batch>.json
Then reply with just the word DONE.

(8 excerpts per batch follow, each headed .)
