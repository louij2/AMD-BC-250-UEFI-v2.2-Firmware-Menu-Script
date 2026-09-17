/* tvui - the small amount of SDL2 that every Steam Link app here needs.
 *
 * The Steam Link is a single-core 1GHz ARMv7 with ~256MB of RAM, driving a
 * TV from across a room. That shapes every choice in here: one font size
 * ladder rather than arbitrary sizes, a title-safe margin so nothing lands
 * under a TV's overscan, and no animation that would cost a frame.
 */
#ifndef TVUI_H
#define TVUI_H

#include <SDL.h>
#include <SDL_ttf.h>

/* The device ships Noto Sans; keep a couple of fallbacks so the app still
 * runs on a desktop build host during development. */
#define TVUI_FONT_PRIMARY "/usr/share/fonts/NotoSans-Regular.ttf"

typedef struct {
    SDL_Window *win;
    SDL_Renderer *ren;
    TTF_Font *font_h1;   /* headings */
    TTF_Font *font_body; /* list rows, values */
    TTF_Font *font_small;/* hints, footers */
    int w, h;            /* drawable size in pixels */
    int safe;            /* title-safe inset, in pixels */
    SDL_GameController *pad;
} tvui;

/* Colours. Dark background because these apps are looked at in a dark room,
 * and a bright full-screen panel on a TV is unpleasant. */
extern const SDL_Color TV_FG;      /* primary text */
extern const SDL_Color TV_DIM;     /* secondary text */
extern const SDL_Color TV_ACCENT;  /* selection, headings */
extern const SDL_Color TV_OK;
extern const SDL_Color TV_WARN;
extern const SDL_Color TV_BAD;

/* Buttons, already folded down from controller + keyboard to one enum so
 * callers never deal with both. */
typedef enum {
    TV_NONE = 0,
    TV_UP,
    TV_DOWN,
    TV_LEFT,
    TV_RIGHT,
    TV_ACCEPT,
    TV_BACK,
    TV_QUIT
} tv_button;

int  tvui_init(tvui *u, const char *title);
void tvui_quit(tvui *u);

/* Blocking-ish input: waits up to timeout_ms for something, TV_NONE if not.
 * Pass 0 to poll. */
tv_button tvui_wait(tvui *u, int timeout_ms);

void tvui_clear(tvui *u);
void tvui_present(tvui *u);

/* Text. Returns the width drawn, so callers can lay things out left to right.
 * x/y are the top-left of the text. */
int tvui_text(tvui *u, TTF_Font *f, int x, int y, SDL_Color c, const char *fmt, ...);
int tvui_text_w(tvui *u, TTF_Font *f, const char *s); /* measure only */
int tvui_line_h(TTF_Font *f);

void tvui_rect(tvui *u, int x, int y, int w, int h, SDL_Color c);

/* A heading plus a rule, drawn at the top of the safe area. Returns the y to
 * start body content at. */
int tvui_header(tvui *u, const char *title);

/* A one-line hint bar along the bottom of the safe area. */
void tvui_footer(tvui *u, const char *hint);

/* Save what has just been drawn to a BMP. Exists so a build host can render
 * the real ARM binary under qemu with SDL_VIDEODRIVER=offscreen and check the
 * layout, fonts and wrapping without a Steam Link attached to a TV. Returns 0
 * on success. */
int tvui_screenshot(tvui *u, const char *path);

#endif /* TVUI_H */
