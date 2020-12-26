all:    harmony charm

harmony:    harmony.preamble harmony.py
	(cat harmony.preamble harmony.py; echo ++++++) > harmony

charm: charm.c json.c global.c ops.c value.c queue.c hashdict.c hashdict.h global.h
	gcc -g charm.c json.c global.c ops.c value.c queue.c hashdict.c -o charm
