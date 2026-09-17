#include "tvui.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

const SDL_Color TV_FG     = { 0xEC, 0xEF, 0xF4, 0xFF };
const SDL_Color TV_DIM    = { 0x92, 0x9A, 0xA8, 0xFF };
const SDL_Color TV_ACCENT = { 0x66, 0xC0, 0xF4, 0xFF };
const SDL_Color TV_OK     = { 0x8F, 0xD4, 0x60, 0xFF };
const SDL_Color TV_WARN   = { 0xE8, 0xC0, 0x4A, 0xFF };
const SDL_Color TV_BAD    = { 0xE2, 0x6D, 0x5A, 0xFF };

/* Font sizes are derived from the panel height so the app is legible whether
 * the Steam Link is driving 720p or 1080p. */
static TTF_Font *open_font(int px)
{
    TTF_Font *f = TTF_OpenFont(TVUI_FONT_PRIMARY, px);
    if (!f) {
        /* Development fallback: whatever the build host has. */
        static const char *alts[] = {
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            NULL
        };
        for (int i = 0; alts[i] && !f; i++)
            f = TTF_OpenFont(alts[i], px);
    }
    return f;
}

int tvui_init(tvui *u, const char *title)
{
    memset(u, 0, sizeof(*u));

    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMECONTROLLER) != 0) {
        SDL_Log("SDL_Init: %s", SDL_GetError());
        return -1;
    }
    if (TTF_Init() != 0) {
        SDL_Log("TTF_Init: %s", TTF_GetError());
        return -1;
    }

    u->win = SDL_CreateWindow(title,
                              SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED,
                              1280, 720,
                              SDL_WINDOW_FULLSCREEN_DESKTOP);
    if (!u->win) {
        SDL_Log("SDL_CreateWindow: %s", SDL_GetError());
        return -1;
    }

    /* No SDL_RENDERER_PRESENTVSYNC: these screens are static, and waiting on
     * vsync just burns the one CPU core we have. */
    u->ren = SDL_CreateRenderer(u->win, -1, SDL_RENDERER_ACCELERATED);
    if (!u->ren)
        u->ren = SDL_CreateRenderer(u->win, -1, 0);
    if (!u->ren) {
        SDL_Log("SDL_CreateRenderer: %s", SDL_GetError());
        return -1;
    }

    SDL_GetRendererOutputSize(u->ren, &u->w, &u->h);
    if (u->w <= 0 || u->h <= 0) { u->w = 1280; u->h = 720; }

    /* 5% inset on each edge. TVs still overscan, and text in the outer few
     * percent of the picture can simply be missing. */
    u->safe = u->h / 20;

    u->font_h1    = open_font(u->h / 14);
    u->font_body  = open_font(u->h / 22);
    u->font_small = open_font(u->h / 30);
    if (!u->font_h1 || !u->font_body || !u->font_small) {
        SDL_Log("no usable font found (wanted %s)", TVUI_FONT_PRIMARY);
        return -1;
    }

    /* Open the first controller that presents itself. The Steam Link forwards
     * the Steam Controller as a standard gamepad. */
    for (int i = 0; i < SDL_NumJoysticks(); i++) {
        if (SDL_IsGameController(i)) {
            u->pad = SDL_GameControllerOpen(i);
            if (u->pad) break;
        }
    }

    SDL_ShowCursor(SDL_DISABLE);
    return 0;
}

void tvui_quit(tvui *u)
{
    if (u->pad) SDL_GameControllerClose(u->pad);
    if (u->font_small) TTF_CloseFont(u->font_small);
    if (u->font_body)  TTF_CloseFont(u->font_body);
    if (u->font_h1)    TTF_CloseFont(u->font_h1);
    if (u->ren) SDL_DestroyRenderer(u->ren);
    if (u->win) SDL_DestroyWindow(u->win);
    TTF_Quit();
    SDL_Quit();
}

static tv_button from_event(tvui *u, const SDL_Event *e)
{
    switch (e->type) {
    case SDL_QUIT:
        return TV_QUIT;

    case SDL_CONTROLLERDEVICEADDED:
        if (!u->pad) u->pad = SDL_GameControllerOpen(e->cdevice.which);
        return TV_NONE;

    case SDL_CONTROLLERDEVICEREMOVED:
        if (u->pad &&
            e->cdevice.which == SDL_JoystickInstanceID(
                SDL_GameControllerGetJoystick(u->pad))) {
            SDL_GameControllerClose(u->pad);
            u->pad = NULL;
        }
        return TV_NONE;

    case SDL_CONTROLLERBUTTONDOWN:
        switch (e->cbutton.button) {
        case SDL_CONTROLLER_BUTTON_DPAD_UP:    return TV_UP;
        case SDL_CONTROLLER_BUTTON_DPAD_DOWN:  return TV_DOWN;
        case SDL_CONTROLLER_BUTTON_DPAD_LEFT:  return TV_LEFT;
        case SDL_CONTROLLER_BUTTON_DPAD_RIGHT: return TV_RIGHT;
        case SDL_CONTROLLER_BUTTON_A:          return TV_ACCEPT;
        case SDL_CONTROLLER_BUTTON_B:          return TV_BACK;
        case SDL_CONTROLLER_BUTTON_START:      return TV_ACCEPT;
        default: return TV_NONE;
        }

    case SDL_KEYDOWN:
        switch (e->key.keysym.sym) {
        case SDLK_UP:     return TV_UP;
        case SDLK_DOWN:   return TV_DOWN;
        case SDLK_LEFT:   return TV_LEFT;
        case SDLK_RIGHT:  return TV_RIGHT;
        case SDLK_RETURN:
        case SDLK_SPACE:  return TV_ACCEPT;
        case SDLK_ESCAPE:
        case SDLK_BACKSPACE: return TV_BACK;
        case SDLK_q:      return TV_QUIT;
        default: return TV_NONE;
        }

    default:
        return TV_NONE;
    }
}

tv_button tvui_wait(tvui *u, int timeout_ms)
{
    SDL_Event e;
    Uint32 deadline = SDL_GetTicks() + (timeout_ms > 0 ? (Uint32)timeout_ms : 0);

    for (;;) {
        while (SDL_PollEvent(&e)) {
            tv_button b = from_event(u, &e);
            if (b != TV_NONE) return b;
        }
        if (timeout_ms <= 0) return TV_NONE;
        if (SDL_TICKS_PASSED(SDL_GetTicks(), deadline)) return TV_NONE;
        SDL_Delay(16);
    }
}

void tvui_clear(tvui *u)
{
    SDL_SetRenderDrawColor(u->ren, 0x15, 0x18, 0x1E, 0xFF);
    SDL_RenderClear(u->ren);
}

void tvui_present(tvui *u) { SDL_RenderPresent(u->ren); }

int tvui_line_h(TTF_Font *f) { return TTF_FontLineSkip(f); }

int tvui_text_w(tvui *u, TTF_Font *f, const char *s)
{
    int w = 0, h = 0;
    (void)u;
    if (TTF_SizeUTF8(f, s, &w, &h) != 0) return 0;
    return w;
}

int tvui_text(tvui *u, TTF_Font *f, int x, int y, SDL_Color c, const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);

    if (!buf[0]) return 0;

    /* Blended rather than solid: solid aliases badly when a TV scales it. */
    SDL_Surface *s = TTF_RenderUTF8_Blended(f, buf, c);
    if (!s) return 0;
    SDL_Texture *t = SDL_CreateTextureFromSurface(u->ren, s);
    int w = s->w, h = s->h;
    SDL_FreeSurface(s);
    if (!t) return 0;

    SDL_Rect dst = { x, y, w, h };
    SDL_RenderCopy(u->ren, t, NULL, &dst);
    SDL_DestroyTexture(t);
    return w;
}

void tvui_rect(tvui *u, int x, int y, int w, int h, SDL_Color c)
{
    SDL_Rect r = { x, y, w, h };
    SDL_SetRenderDrawColor(u->ren, c.r, c.g, c.b, c.a);
    SDL_RenderFillRect(u->ren, &r);
}

int tvui_header(tvui *u, const char *title)
{
    int y = u->safe;
    tvui_text(u, u->font_h1, u->safe, y, TV_FG, "%s", title);
    y += tvui_line_h(u->font_h1);
    tvui_rect(u, u->safe, y, u->w - 2 * u->safe, 2, TV_ACCENT);
    return y + tvui_line_h(u->font_body);
}

void tvui_footer(tvui *u, const char *hint)
{
    int lh = tvui_line_h(u->font_small);
    tvui_text(u, u->font_small, u->safe, u->h - u->safe - lh, TV_DIM, "%s", hint);
}

int tvui_screenshot(tvui *u, const char *path)
{
    SDL_Surface *s = SDL_CreateRGBSurfaceWithFormat(0, u->w, u->h, 32,
                                                    SDL_PIXELFORMAT_ARGB8888);
    if (!s) {
        SDL_Log("screenshot: %s", SDL_GetError());
        return -1;
    }
    if (SDL_RenderReadPixels(u->ren, NULL, s->format->format,
                             s->pixels, s->pitch) != 0) {
        SDL_Log("screenshot: RenderReadPixels: %s", SDL_GetError());
        SDL_FreeSurface(s);
        return -1;
    }
    int rc = SDL_SaveBMP(s, path);
    if (rc != 0) SDL_Log("screenshot: SaveBMP: %s", SDL_GetError());
    SDL_FreeSurface(s);
    return rc;
}
