# Built with the Steam Link SDK toolchain; source setenv.sh first, or just
# run ./build_steamlink.sh which does it for you.

APP     = bc250-status
COMMON  = ../common

CFLAGS  += -O2 -Wall -Wextra -std=gnu99
CFLAGS  += -I$(MARVELL_ROOTFS)/usr/include/SDL2 -I$(COMMON)
LDLIBS  += -lSDL2 -lSDL2_ttf

OBJS    = main.o tvui.o promq.o

$(APP): $(OBJS)
	$(CC) -o $@ $(OBJS) $(LDLIBS)

tvui.o: $(COMMON)/tvui.c $(COMMON)/tvui.h
	$(CC) $(CFLAGS) -c -o $@ $(COMMON)/tvui.c

promq.o: $(COMMON)/promq.c $(COMMON)/promq.h
	$(CC) $(CFLAGS) -c -o $@ $(COMMON)/promq.c

main.o: main.c $(COMMON)/tvui.h $(COMMON)/promq.h
	$(CC) $(CFLAGS) -c -o $@ main.c

clean:
	rm -f $(APP) $(OBJS)
	rm -rf steamlink

.PHONY: clean
