# CSGO ZE Server Finder



## Inspiration

The website I used to find CSGO ZE servers disappeared, and most alternatives miss Chinese servers, include servers I don’t join, or are full of ads. So I built my own tool to scan and list my favorite ZE servers, and let me join them with one click.



## Features

- Scan servers from `server\_list.txt`

- Show server name, map, players, and ping

- Web UI with clickable IPs (`steam://connect/...`) to join instantly

- Save results to `servers\_output.csv`



## Requirements

- Windows (only tested on Windows)

- Python \*\*3.10+\*\*

- Dependencies installed automatically via:

pip install -r requirements.txt



## server\_list.txt Format

Each line = `IP\[:PORT] | ServerName` (server name optional). Examples:

74.91.124.21:27015 | GFL

192.0.2.123:27016

203.0.113.5:27015 | My ZE Server



## Quick Start

1. Install Python 3.10+

2. Clone/download this project

3. (Optional) create virtual env:

python -m venv venv

.\\venv\\Scripts\\activate

4. Install requirements:

pip install -r requirements.txt

5. Edit `server\_list.txt` with your servers

6. Run the program:

- Double-click \*\*run.bat\*\*, \*\*or\*\*

- Run manually:

&nbsp; ```

&nbsp; python web\_view.py

&nbsp; ```

7. Open browser at `http://127.0.0.1:5000/`



Click a server IP → Steam opens → join game.









