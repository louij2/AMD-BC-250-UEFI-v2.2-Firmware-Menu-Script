/* bc250-fix - trigger the BC-250's recovery actions from the TV.
 *
 * The BC-250 already knows how to fix itself: bc250_watchdog.py can wake
 * Steam out of a stuck sleep and can restart the Game Mode session, with
 * guard rails around both. What it could not do was be *asked* from the
 * sofa. Until now that meant finding a laptop whenever the screen went
 * black.
 *
 * So this app is deliberately thin. It does not reimplement any recovery
 * logic; it runs the same entry points over SSH and reports what came back.
 * Every guard rail (the 75s rule that stops SteamOS moving ~/.steam aside,
 * the reset cooldown, the "is it actually asleep" test) therefore still
 * applies, because the decision is still made on the BC-250.
 *
 * Connection settings live in settings.conf next to the binary, which is not
 * in git: the repository is public and the addresses are not.
 */
#include "tvui.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define MAX_LINE 512

typedef struct {
    char host[128];
    char user[64];
    char key[256];
    int  timeout;      /* ssh connect timeout, seconds */
} config;

typedef struct {
    const char *label;
    const char *detail;
    const char *flag;     /* NULL for Exit */
    SDL_Color   tone;
} action;

static const action ACTIONS[] = {
    { "Fix it",
      "Wake a stuck sleep if that's the fault, otherwise reset Game Mode.",
      "fix",     { 0x8F, 0xD4, 0x60, 0xFF } },
    { "Wake Steam only",
      "Steam asked to sleep and never came back. Closes nothing.",
      "wake",    { 0x66, 0xC0, 0xF4, 0xFF } },
    { "Reset Game Mode",
      "Restarts the session. Any running game is closed.",
      "reset",   { 0xE8, 0xC0, 0x4A, 0xFF } },
    { "Exit", "Back to the Steam Link menu.", NULL, { 0x92, 0x9A, 0xA8, 0xFF } },
};
static const int N_ACTIONS = (int)(sizeof(ACTIONS) / sizeof(ACTIONS[0]));

/* ------------------------------------------------------------------ config */

static void trim(char *s)
{
    char *p = s + strlen(s);
    while (p > s && (p[-1] == '\n' || p[-1] == '\r' || p[-1] == ' ' || p[-1] == '\t'))
        *--p = '\0';
    p = s;
    while (*p == ' ' || *p == '\t') p++;
    if (p != s) memmove(s, p, strlen(p) + 1);
}

/* Look for settings.conf beside the executable, so the app works the same
 * whether it was launched by Steam Link's menu or by hand over SSH. */
static void config_path(char *out, size_t n)
{
    char *base = SDL_GetBasePath();
    if (base) {
        snprintf(out, n, "%ssettings.conf", base);
        SDL_free(base);
    } else {
        snprintf(out, n, "settings.conf");
    }
}

static int config_load(config *c, char *err, size_t errn)
{
    char path[MAX_LINE - 64];  /* leaves room for the error prefix */
    config_path(path, sizeof(path));

    /* Defaults that are safe to have in a public repo. */
    c->user[0] = '\0';
    c->host[0] = '\0';
    snprintf(c->key, sizeof(c->key), "%s", "");
    c->timeout = 8;

    FILE *f = fopen(path, "r");
    if (!f) {
        snprintf(err, errn, "No settings.conf at %s", path);
        return -1;
    }

    char line[MAX_LINE];
    while (fgets(line, sizeof(line), f)) {
        trim(line);
        if (!line[0] || line[0] == '#') continue;
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        char *k = line, *v = eq + 1;
        trim(k); trim(v);
        if      (!strcmp(k, "host"))    snprintf(c->host,   sizeof(c->host),   "%s", v);
        else if (!strcmp(k, "user"))    snprintf(c->user,   sizeof(c->user),   "%s", v);
        else if (!strcmp(k, "key"))     snprintf(c->key,    sizeof(c->key),    "%s", v);
        else if (!strcmp(k, "timeout")) c->timeout = atoi(v);
    }
    fclose(f);

    if (!c->host[0] || !c->user[0]) {
        snprintf(err, errn, "settings.conf needs both host= and user=");
        return -1;
    }
    if (c->timeout < 2) c->timeout = 2;
    return 0;
}

/* ------------------------------------------------------------------ action */

/* Ask the BC-250 to perform one recovery action and keep the last line it
 * printed.
 *
 * Only a bare action word goes over the wire ("fix", "wake", "reset"). The
 * key is pinned on the BC-250 to bc250-recovery-remote, which validates that
 * word and runs the watchdog itself, so there is no remote shell command to
 * quote here and a stolen key cannot do anything else.
 *
 * The watchdog exits 0 when it carried the action out and 1 when it refused
 * or failed, printing a one-line reason either way, so the exit code and that
 * line are all the UI needs. ssh's own failures show up as 255.
 */
static int run_action(const config *c, const char *action,
                      char *out, size_t outn)
{
    char cmd[1200];
    char keyopt[300] = "";

    if (c->key[0])
        snprintf(keyopt, sizeof(keyopt), "-i '%s' ", c->key);

    /* ConnectTimeout bounds the unreachable case; ServerAliveInterval bounds
     * the worse one, where the BC-250 answers the TCP handshake and then
     * stops responding because the very fault we are fixing has wedged it. */
    snprintf(cmd, sizeof(cmd),
             "ssh %s-o BatchMode=yes -o StrictHostKeyChecking=accept-new "
             "-o ConnectTimeout=%d -o ServerAliveInterval=3 "
             "-o ServerAliveCountMax=4 '%s@%s' %s 2>&1",
             keyopt, c->timeout, c->user, c->host, action);

    out[0] = '\0';
    FILE *p = popen(cmd, "r");
    if (!p) {
        snprintf(out, outn, "could not start ssh");
        return -1;
    }

    char line[MAX_LINE];
    while (fgets(line, sizeof(line), p)) {
        trim(line);
        if (line[0]) snprintf(out, outn, "%s", line);
    }

    int rc = pclose(p);
    int code = (rc == -1) ? -1 : (rc >> 8 & 0xFF);

    /* Leave ssh's own wording alone and let the screen say what it means --
     * rewriting the string in place here just risks truncating the one line
     * that explains the failure. */
    if (!out[0])
        snprintf(out, outn, code == 0 ? "done" :
                            code == 255 ? "ssh gave no reason" : "refused");

    return code;
}

/* ------------------------------------------------------------------ screens */

static void draw_menu(tvui *u, const config *c, int sel)
{
    tvui_clear(u);
    int y = tvui_header(u, "Fix the console");

    tvui_text(u, u->font_small, u->safe, y, TV_DIM,
              "%s@%s", c->user, c->host);
    y += tvui_line_h(u->font_small) * 2;

    int row = tvui_line_h(u->font_body) + tvui_line_h(u->font_small)
            + tvui_line_h(u->font_small) / 2;

    for (int i = 0; i < N_ACTIONS; i++) {
        int selected = (i == sel);
        if (selected) {
            SDL_Color bar = { 0x20, 0x2A, 0x36, 0xFF };
            tvui_rect(u, u->safe - u->safe / 3, y - row / 8,
                      u->w - 2 * u->safe + 2 * (u->safe / 3), row, bar);
            tvui_rect(u, u->safe - u->safe / 3, y - row / 8,
                      6, row, ACTIONS[i].tone);
        }
        tvui_text(u, u->font_body, u->safe, y,
                  selected ? ACTIONS[i].tone : TV_FG, "%s", ACTIONS[i].label);
        tvui_text(u, u->font_small, u->safe,
                  y + tvui_line_h(u->font_body),
                  TV_DIM, "%s", ACTIONS[i].detail);
        y += row;
    }

    tvui_footer(u, "D-pad to move    A to run    B to exit");
    tvui_present(u);
}

static void draw_busy(tvui *u, const char *label)
{
    tvui_clear(u);
    int y = tvui_header(u, "Fix the console");
    tvui_text(u, u->font_body, u->safe, y, TV_ACCENT, "%s...", label);
    tvui_text(u, u->font_small, u->safe, y + tvui_line_h(u->font_body) * 2,
              TV_DIM, "Asking the BC-250 to do it.");
    tvui_present(u);
}

/* Wrap a message onto however many lines the safe width allows. Long enough
 * to matter: ssh errors are wordy and truncating them hides the cause. */
static void draw_wrapped(tvui *u, int x, int y, SDL_Color c, const char *msg)
{
    /* Lengths are tracked explicitly rather than rebuilding the line with
     * snprintf each word: the candidate line is always "line + space + word",
     * and carrying the length makes it obvious that it cannot overrun. */
    int maxw = u->w - 2 * u->safe;
    char line[MAX_LINE];
    size_t len = 0;
    const char *p = msg;

    line[0] = '\0';

    while (*p) {
        const char *sp = strchr(p, ' ');
        size_t wlen = sp ? (size_t)(sp - p) : strlen(p);

        /* A single word longer than a whole line is pathological; clip it so
         * the rest of the message still gets shown. */
        if (wlen > MAX_LINE / 4) wlen = MAX_LINE / 4;

        size_t need = len + (len ? 1 : 0) + wlen;

        if (need < sizeof(line)) {
            char trial[MAX_LINE];
            size_t at = 0;
            memcpy(trial, line, len);
            at = len;
            if (len) trial[at++] = ' ';
            memcpy(trial + at, p, wlen);
            at += wlen;
            trial[at] = '\0';

            if (tvui_text_w(u, u->font_small, trial) <= maxw || len == 0) {
                memcpy(line, trial, at + 1);
                len = at;
                p += wlen;
                while (*p == ' ') p++;
                continue;
            }
        }

        /* Does not fit: flush what we have and start again with this word. */
        tvui_text(u, u->font_small, x, y, c, "%s", line);
        y += tvui_line_h(u->font_small);
        memcpy(line, p, wlen);
        line[wlen] = '\0';
        len = wlen;

        p += wlen;
        while (*p == ' ') p++;
    }

    if (len) tvui_text(u, u->font_small, x, y, c, "%s", line);
}

static void draw_result(tvui *u, const config *c, const action *a,
                        int code, const char *msg)
{
    tvui_clear(u);
    int y = tvui_header(u, "Fix the console");

    /* Three outcomes worth telling apart on a TV: it worked, the BC-250
     * looked and decided nothing was wrong, or we never reached it at all.
     * The last one is a different problem and deserves a different headline. */
    const char *headline;
    SDL_Color tone;
    if (code == 0)          { headline = "Done";        tone = TV_OK; }
    else if (code == 1)     { headline = "Not needed";  tone = TV_WARN; }
    else if (code == 255)   { headline = "No answer";   tone = TV_BAD; }
    else                    { headline = "Failed";      tone = TV_BAD; }

    tvui_text(u, u->font_body, u->safe, y, tone, "%s - %s", headline, a->label);
    y += tvui_line_h(u->font_body);

    if (code == 255) {
        tvui_text(u, u->font_small, u->safe, y, TV_DIM,
                  "Could not reach %s.", c->host);
        y += tvui_line_h(u->font_small);
    }
    y += tvui_line_h(u->font_small) / 2;

    draw_wrapped(u, u->safe, y, TV_DIM, msg);

    tvui_footer(u, "A or B to go back");
    tvui_present(u);
}

static void draw_fatal(tvui *u, const char *msg)
{
    tvui_clear(u);
    int y = tvui_header(u, "Fix the console");
    tvui_text(u, u->font_body, u->safe, y, TV_BAD, "Not configured");
    draw_wrapped(u, u->safe, y + tvui_line_h(u->font_body) * 3 / 2, TV_DIM, msg);
    tvui_footer(u, "Any button to exit");
    tvui_present(u);
}

/* ------------------------------------------------------------------- main */

int main(int argc, char *argv[])
{
    /* --screenshot <file>: draw one frame, save it, exit. For checking the
     * layout on a build host without a Steam Link and a TV. */
    const char *shot = NULL;
    for (int i = 1; i < argc - 1; i++)
        if (!strcmp(argv[i], "--screenshot")) shot = argv[i + 1];

    tvui u;
    if (tvui_init(&u, "Fix the console") != 0)
        return 1;

    config c;
    char err[MAX_LINE];
    if (config_load(&c, err, sizeof(err)) != 0) {
        if (shot) {
            draw_fatal(&u, err);
            int rc = tvui_screenshot(&u, shot);
            tvui_quit(&u);
            return rc == 0 ? 0 : 1;
        }
        draw_fatal(&u, err);
        for (;;) {
            tv_button b = tvui_wait(&u, 200);
            if (b != TV_NONE) break;
        }
        tvui_quit(&u);
        return 1;
    }

    int sel = 0;

    if (shot) {
        draw_menu(&u, &c, sel);
        int rc = tvui_screenshot(&u, shot);
        tvui_quit(&u);
        return rc == 0 ? 0 : 1;
    }

    for (;;) {
        draw_menu(&u, &c, sel);

        tv_button b = tvui_wait(&u, 200);
        if (b == TV_NONE) continue;
        if (b == TV_QUIT || b == TV_BACK) break;

        if (b == TV_UP)   sel = (sel - 1 + N_ACTIONS) % N_ACTIONS;
        if (b == TV_DOWN) sel = (sel + 1) % N_ACTIONS;

        if (b == TV_ACCEPT) {
            const action *a = &ACTIONS[sel];
            if (!a->flag) break;  /* Exit */

            draw_busy(&u, a->label);

            char msg[MAX_LINE];
            int code = run_action(&c, a->flag, msg, sizeof(msg));

            /* Resetting Game Mode tears down the compositor this app is
             * drawing on, so there may be nothing left to show. Say so
             * before it happens rather than appearing to hang. */
            draw_result(&u, &c, a, code, msg);

            for (;;) {
                tv_button k = tvui_wait(&u, 200);
                if (k == TV_ACCEPT || k == TV_BACK || k == TV_QUIT) break;
            }
        }
    }

    tvui_quit(&u);
    return 0;
}
