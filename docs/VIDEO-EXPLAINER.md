# bring it home: "how it works" voiceover (Devpost video)

## The short version (use this): about 25 seconds, said right after the demo

> Every bus and train position from MARTA's live feeds is recorded on our Vultr server into Tiger Data, so two taps
> find the exact train you were on. Gemini turns a spoken sentence into your stop and time, but every decision comes
> from real transit data. Our planner, tested on recorded rides, picks the fastest way back about 97% of the time.
> Find My shows where it was. bring it home shows where it is.

For about 20 seconds, drop the Gemini sentence.

## The long version (about 85 seconds), if there is room

Plays right after the product demo. About 85 seconds at a calm pace (about 230 words). Every number here is from
docs/BACKTEST.md (data recorded during the hackathon) or this morning's live test.

| Time | On screen | Voiceover |
|---|---|---|
| 0:00 | The architecture diagram (judge guide, section 3) | "So how does it know where your bag is?" |
| 0:04 | MARTA bus and train dots moving on a map | "MARTA publishes live positions for every bus and live arrival predictions for every train. Our recorders, written in Python and running around the clock on a **Vultr** server, save every update, every 15 seconds for buses and 20 for trains." |
| 0:16 | The database diagram, then the match screen | "Everything goes into **Tiger Data**: TimescaleDB for time, PostGIS for place. So one query can ask: which vehicles passed within a quarter mile of your stop, in the minutes you remember? That's how two taps become one exact train. And we track the vehicle itself, so we know where it is right now." |
| 0:32 | "Say it in one sentence" being used | "If you'd rather just say it, **Gemini** turns one sentence into a stop, a line and a time, and our own code checks it against real stops. Gemini also writes your lost-item email and call script. But it never decides anything. It can't invent a stop, a time or a train, and if it's slow, simple rules take over." |
| 0:48 | "Your best plan" card, then the live map | "The plan itself is pure data. We measured how MARTA trains and buses really turn around at the end of the line, and built a planner that compares every way back: walk ahead, wait for it to come back, or ride ahead and switch platforms. We replayed our recordings through it, and its first choice was truly the fastest way back about 97% of the time. This morning, live, it called our train's return 22 minutes ahead, and it arrived within 30 seconds." |
| 1:10 | The app on a phone, then on a laptop | "The app is a **Next.js** progressive web app on **Vercel**, with a **MapLibre** map, and the API is **FastAPI**. It works on any phone, even a borrowed one: no login, no download. Your location is used only for your plan and never stored, and everything is deleted within a day." |
| 1:22 | Logo | "Find My shows where it was. bring it home shows where it is." |

## Notes for recording

- Say the name in lowercase spirit: "bring it home".
- Keep the "it never decides anything" line. It answers the first question judges ask about the AI part.
- If you are short on time, cut the FastAPI clause and the laptop shot first.
- If you mention the domain: www.bringit.casa.
