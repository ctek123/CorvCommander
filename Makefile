.PHONY: venv dev run list build clean release
venv:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -U pip wheel

dev: venv
	. .venv/bin/activate && pip install -r requirements.txt

# Pass extra flags: make run ARGS="--beep --always-on"
run:
	. .venv/bin/activate && python beevette.py $(ARGS)

list:
	. .venv/bin/activate && python beevette.py --list-devices

# Note: ':' is the data-sep on POSIX; on Windows use ';'
build:
	. .venv/bin/activate && pyinstaller --onefile \
		--name VetteBee-Comms \
		--add-data "config.toml:." \
		beevette.py

clean:
	rm -rf .venv build dist __pycache__ *.spec
