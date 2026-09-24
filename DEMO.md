# Running the demo locally

## One-time setup

```bash
# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate   # if not already set up
pip install -r requirements.txt
cd ..
cp .env.example .env   # fill in SECRET_KEY and PASSWORD_PEPPER with any value for local demo use
cd backend && alembic upgrade head && cd ..

# Frontend
cd frontend && npm install && cd ..
```

## Every time you want a fresh demo

```bash
# From the project root, with the backend venv activated:
rm -f data/rdopt.db
cd backend && alembic upgrade head && cd ..
python3 scripts/seed_demo_case.py
```

This creates one demo organization ("Demo Coatings Co"), one fully-worked
change case ("AquaShield 400 -- PFAS Fluorosurfactant Replacement"), 20
historical formulation trials, and 4 ranked candidates -- already ranked,
so the demo can start mid-workflow if you want to jump straight to the
results, or you can re-add a 5th candidate live to show the ranking step
happening in real time.

Login for the demo: `demo@democoatings.com` / `demo-password-for-local-use-only-123`

## Start both servers

```bash
# Terminal 1
cd backend && uvicorn backend.app.main:app --reload --port 5050

# Terminal 2
cd frontend && npm run dev
```

Open `http://localhost:3000`, log in with the demo credentials above.

## The 10-minute walkthrough

1. **(1 min) Set the scene.** Log in, go to **Change Cases**. Point out the case name and trigger: a real, standard industrial coating (fictional company, realistic scenario) forced to replace a PFAS-based fluorosurfactant leveling agent.

2. **(2 min) Open the case.** Show the uploaded historical dataset (20 real-shaped formulation trials) and the qualification spec: salt-spray corrosion resistance per ASTM B117, target ≥500 hours to first rust -- a real, standard test method, not an invented metric.

3. **(2 min) Show the candidates.** Four named PFAS-free alternatives, each with different formulation parameters. If you want to show live input, delete one and re-add it here instead of just pointing at existing ones.

4. **(3 min) The core moment -- click "Run ranking."** Walk through the result:
   - **PolyGuard NF-22: ~88% predicted, moderate uncertainty.** "This is your strongest bet -- test this first."
   - **EcoShield SF-100: ~59% predicted, but nearly double the uncertainty of PolyGuard.** This is the most important line to explain out loud: *"This candidate's raw prediction is actually decent, but it sits outside the range of formulations we have real historical data for -- so the model is honestly telling you it's less sure, not just giving you a lower number."* This is the single best moment to demonstrate that the tool isn't a black box guessing at confidence -- it's a calibrated model that knows what it doesn't know.
   - **HydroFlex and GreenCoat: ~0%.** "Both of these are predicted to fall well short of spec based on how similar formulations performed historically -- probably not worth committing lab time to unless nothing else works out."

5. **(1 min) Recommended experiments.** Show that each candidate has a specific next physical step recommended -- and point out the report language explicitly: *this identifies what to test first, it doesn't replace running and recording the real test.*

6. **(1 min) Download the report.** Open the generated `.docx` -- show the ranked table and, specifically, the disclaimer paragraph distinguishing model prediction from physical validation. This is worth reading aloud once: it's the sentence that makes this defensible to show an auditor or an OEM, not just internally useful.

## What NOT to claim during the demo

- Never say physical qualification is complete or fast -- the diagnostic is the analysis, not the lab work.
- Never say the prediction is guaranteed or 100% accurate -- the whole value of the uncertainty column is that it's honest about what isn't known yet.
- If asked "how is this different from what our own R&D team already does," the honest answer demonstrated live: it's not replacing their formulation science, it's replacing the *guesswork in deciding which candidate to spend limited physical-testing time on first* -- using their own historical data, not a generic industry model.
