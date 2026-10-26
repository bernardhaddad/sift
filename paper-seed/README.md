# paper-seed

The hourly paper book, published with every finished cycle in the same commit as `data.json`: `state.json` (every book's open positions, closed trades and entry cooldowns), `cycle_meta.json` (the cycle counter) and `flow_log.jsonl`. The cycle runs on the book in the Actions cache when it is readable and not behind `data.json`, and on this one otherwise, so a failed cache save can never republish an older book.
