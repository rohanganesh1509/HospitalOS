# HospitalOS — Doctor Assignment Upgrade

Educational Flask + SQLite Operating Systems project. This build preserves the CPU scheduling simulator (FCFS, non-preemptive SJF, Priority) and Banker's Algorithm Deadlock Lab, and adds doctor management with automatic patient assignment.

## New features
- Add and edit doctors, set specialization, workload capacity, and on-duty status.
- Automatically assign a new patient to an on-duty doctor with matching specialization and free capacity.
- Balance assignment by choosing the eligible doctor with the fewest active patients (ties go to the lower doctor ID).
- Multiple active patients per doctor, limited by configured capacity.
- Unassign a patient, mark a patient completed, and retry assignment for unassigned patients.
- Doctor workload dashboard and patient-to-doctor display.
- Existing `hospitalos.db` patient records are preserved; the app adds missing columns automatically.
- On first startup where no doctors table exists, three fictional demo doctors are created. No sample patients are inserted.

## Run in Windows PowerShell / Command Prompt
1. Extract the ZIP.
2. Open a terminal in the `HospitalOS` folder.
3. Create and activate an environment (optional but recommended):
   ```bat
   py -m venv .venv
   .venv\Scripts\activate
   ```
4. Install dependencies and start:
   ```bat
   pip install -r requirements.txt
   python app.py
   ```
5. Open `http://127.0.0.1:5000/`.

## Run in WSL / Ubuntu
```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 app.py
```
If venv creation says `ensurepip` is unavailable, install the matching venv package first, e.g. `sudo apt install python3.12-venv` (use the Python version shown by `python3 --version`).

## Upgrade an existing project safely
1. Stop the running Flask server with Ctrl+C.
2. Back up the project folder (recommended).
3. Copy the files and folders in this ZIP into the existing `HospitalOS` project folder and choose **Replace** for matching code/template/static files.
4. **Do not delete or replace `hospitalos.db` or `.venv`.** The updated app migrates the existing database on startup.
5. Activate your existing virtual environment, then run `python3 app.py` (WSL) or `python app.py` (Windows).
6. Open `/` for the dashboard, `/doctors` for doctor management, and `/resources` for the Deadlock Lab.

## Automatic assignment rules
- Patient specialization must match the doctor's specialization.
- Doctor must be on duty and have an open capacity slot.
- The eligible doctor with the fewest active (not completed) patients is selected.
- If no matching doctor can accept the patient, the patient remains unassigned. Add/edit an appropriate doctor, then use **Retry automatic assignment**.
- Taking a doctor off duty unassigns that doctor's active patients so they can be reassigned. Completed patients remain in the history and do not count against active workload.

## Scope
This is a teaching simulation, not a real hospital information system. Do not use real patient records or rely on it for clinical decisions.

## Deploy publicly with GitHub + Render
1. Upload the contents of this `HospitalOS` folder to a GitHub repository. The repository root should contain `app.py`, `algorithms.py`, `requirements.txt`, `Procfile`, `templates/`, and `static/`.
2. Do not upload `.venv/`, `.env`, or `hospitalos.db`. The app creates a fresh SQLite database when it starts.
3. In Render, create a **New Web Service** and connect the GitHub repository.
4. Choose the Python runtime. Set **Build Command** to `pip install -r requirements.txt` and **Start Command** to `gunicorn app:app`.
5. Deploy and open the `onrender.com` URL Render provides.

Important: this starter uses a local SQLite file. On typical free web hosting, local files can be reset during redeploys/restarts, so patient and doctor records may not persist. For persistent records, configure a persistent disk and a database path on that disk, or migrate to a managed database. Use only fictional demo data on a public student project; this is not a real clinical system.
