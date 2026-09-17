/* bc250-status - a read-only health screen for the TV.
 *
 * Four questions, answered from the sofa: is anything down, is the BC-250 on
 * 6 or 8 cores, is LanCache actually caching, and how good is the link
 * between this Steam Link and the console.
 *
 * The first three come from Prometheus, which already scrapes all of it. The
 * fourth is measured here rather than read from a metric, and that is
 * deliberate: Steam only logs a per-session ping when the BC-250 is the
 * *client* of a stream, so there is no host-side RTT metric for sessions
 * streamed *to* this box -- steamlink_rtt_seconds has never existed in this
 * Prometheus. Measuring the TCP handshake from the Steam Link itself gives a
 * real number for the path that actually matters, and it is honest about what
 * it is: latency to the console right now, not a replay of the last stream.
 *
 * Nothing here can change anything. It only reads.
 */
#include "tvui.h"
#include "promq.h"

#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#define MAX_LINE 512
#define RTT_SAMPLES 3

typedef struct {
    char prom[256];      /* base URL of Prometheus, no trailing slash */
    char host[128];      /* BC-250 LAN address, for the RTT probe */
    int  port;           /* a TCP port that is open on it */
    int  refresh;        /* seconds between refreshes */
    int  timeout;        /* per-query timeout, seconds */
} config;

/* One row on screen. value is pre-formatted; a NULL note prints nothing. */
typedef struct {
    char label[64];
    char value[64];
    char note[96];
    SDL_Color tone;
    int  ok;             /* 0 = could not be read */
} row;

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

static int config_load(config *c, char *err, size_t errn)
{
    char path[MAX_LINE - 64];
    char *base = SDL_GetBasePath();
    if (base) {
        snprintf(path, sizeof(path), "%ssettings.conf", base);
        SDL_free(base);
    } else {
        snprintf(path, sizeof(path), "settings.conf");
    }

    c->prom[0] = '\0';
    c->host[0] = '\0';
    c->port = 22;
    c->refresh = 15;
    c->timeout = 5;

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
        if      (!strcmp(k, "prometheus")) snprintf(c->prom, sizeof(c->prom), "%s", v);
        else if (!strcmp(k, "host"))       snprintf(c->host, sizeof(c->host), "%s", v);
        else if (!strcmp(k, "port"))       c->port = atoi(v);
        else if (!strcmp(k, "refresh"))    c->refresh = atoi(v);
        else if (!strcmp(k, "timeout"))    c->timeout = atoi(v);
    }
    fclose(f);

    /* Strip a trailing slash so URLs do not end up with a double one. */
    size_t pl = strlen(c->prom);
    while (pl && c->prom[pl - 1] == '/') c->prom[--pl] = '\0';

    if (!c->prom[0]) {
        snprintf(err, errn, "settings.conf needs prometheus=");
        return -1;
    }
    if (c->refresh < 5) c->refresh = 5;
    if (c->timeout < 2) c->timeout = 2;
    if (c->port < 1 || c->port > 65535) c->port = 22;
    return 0;
}

/* Prometheus reading lives in common/promq.c, which has no SDL dependency so
 * its hand-rolled JSON handling can be unit-tested on the build host. */

static int prom_query(const config *c, const char *expr, double *out, int *empty)
{
    promq_result r = promq_query(c->prom, expr, c->timeout, out);
    if (empty) *empty = (r == PROMQ_EMPTY);
    return r == PROMQ_OK ? 0 : -1;
}

/* --------------------------------------------------------------- rtt probe */

/* Time a TCP handshake to the console. Cheaper and more honest than ICMP
 * here: the Steam Link app is not root, so it cannot open a raw socket, and
 * the handshake traverses exactly the path a stream would.
 *
 * Returns the best of a few samples in milliseconds, or -1. Best rather than
 * mean because a single scheduling hiccup on a 1GHz core should not be
 * reported as network latency.
 */
static double tcp_rtt_ms(const config *c)
{
    char portstr[16];
    snprintf(portstr, sizeof(portstr), "%d", c->port);

    struct addrinfo hints, *ai = NULL;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;

    if (getaddrinfo(c->host, portstr, &hints, &ai) != 0 || !ai)
        return -1;

    double best = -1;

    for (int i = 0; i < RTT_SAMPLES; i++) {
        int fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
        if (fd < 0) continue;

        int flags = fcntl(fd, F_GETFL, 0);
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);

        struct timeval t0, t1;
        gettimeofday(&t0, NULL);

        int r = connect(fd, ai->ai_addr, ai->ai_addrlen);
        if (r != 0 && errno == EINPROGRESS) {
            fd_set w;
            FD_ZERO(&w);
            FD_SET(fd, &w);
            struct timeval tv = { c->timeout, 0 };
            r = select(fd + 1, NULL, &w, NULL, &tv);
            if (r > 0) {
                int soerr = 0;
                socklen_t sl = sizeof(soerr);
                if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &soerr, &sl) != 0 || soerr != 0)
                    r = -1;
                else
                    r = 0;
            } else {
                r = -1;
            }
        }

        gettimeofday(&t1, NULL);
        close(fd);

        if (r == 0) {
            double ms = (t1.tv_sec - t0.tv_sec) * 1000.0
                      + (t1.tv_usec - t0.tv_usec) / 1000.0;
            if (best < 0 || ms < best) best = ms;
        }
    }

    freeaddrinfo(ai);
    return best;
}

/* ----------------------------------------------------------------- gather */

static void set_row(row *r, const char *label, SDL_Color tone)
{
    memset(r, 0, sizeof(*r));
    snprintf(r->label, sizeof(r->label), "%s", label);
    snprintf(r->value, sizeof(r->value), "%s", "unavailable");
    r->tone = tone;
    r->ok = 0;
}

#define N_ROWS 4

static void gather(const config *c, row rows[N_ROWS])
{
    double up = 0, total = 0, down = 0, threads = 0, hit = 0, active = 0;
    int empty = 0;

    /* --- hosts ------------------------------------------------------ */
    set_row(&rows[0], "Hosts", TV_FG);
    if (prom_query(c, "sum(up)", &up, &empty) == 0 &&
        prom_query(c, "count(up)", &total, &empty) == 0) {
        /* count(up == 0) returns an empty result when nothing is down, which
         * is not an error -- it is the good case. */
        if (prom_query(c, "count(up == 0)", &down, &empty) != 0)
            down = empty ? 0 : -1;

        rows[0].ok = 1;
        snprintf(rows[0].value, sizeof(rows[0].value), "%d of %d up",
                 (int)up, (int)total);
        if (down > 0) {
            snprintf(rows[0].note, sizeof(rows[0].note),
                     "%d target%s down", (int)down, down == 1 ? "" : "s");
            rows[0].tone = TV_BAD;
        } else if (down == 0) {
            snprintf(rows[0].note, sizeof(rows[0].note), "everything answering");
            rows[0].tone = TV_OK;
        } else {
            rows[0].tone = TV_WARN;
        }
    }

    /* --- BC-250 cores ----------------------------------------------- */
    set_row(&rows[1], "Console CPU", TV_FG);
    if (prom_query(c,
            "count(count by (cpu) (node_cpu_seconds_total{job=\"bc250\"}))",
            &threads, &empty) == 0 && threads > 0) {
        /* SMT is on, so the BIOS core count is half the thread count. The
         * BC-250 is configured for either 6 or 8 cores. */
        int cores = (int)(threads / 2);
        rows[1].ok = 1;
        snprintf(rows[1].value, sizeof(rows[1].value), "%d cores", cores);
        snprintf(rows[1].note, sizeof(rows[1].note),
                 "%d threads online", (int)threads);
        rows[1].tone = (cores >= 8) ? TV_OK : TV_WARN;
    }

    /* --- LanCache --------------------------------------------------- */
    set_row(&rows[2], "LanCache", TV_FG);
    if (prom_query(c, "lancache_cache_hit_ratio", &hit, &empty) == 0) {
        rows[2].ok = 1;
        snprintf(rows[2].value, sizeof(rows[2].value), "%.1f%% hit rate",
                 hit * 100.0);
        snprintf(rows[2].note, sizeof(rows[2].note),
                 "share of bytes served from cache");
        rows[2].tone = (hit >= 0.5) ? TV_OK : (hit >= 0.1 ? TV_WARN : TV_DIM);
    }

    /* --- link to the console ---------------------------------------- */
    set_row(&rows[3], "Link to console", TV_FG);
    if (c->host[0]) {
        double ms = tcp_rtt_ms(c);
        if (ms >= 0) {
            rows[3].ok = 1;
            snprintf(rows[3].value, sizeof(rows[3].value), "%.1f ms", ms);
            rows[3].tone = (ms < 5) ? TV_OK : (ms < 20 ? TV_WARN : TV_BAD);
        } else {
            snprintf(rows[3].value, sizeof(rows[3].value), "no answer");
            rows[3].tone = TV_BAD;
        }

        /* Whether a stream is running is worth saying next to the latency,
         * because an idle number and an in-use number mean different things. */
        if (prom_query(c, "steamlink_streaming_active", &active, &empty) == 0)
            snprintf(rows[3].note, sizeof(rows[3].note), "%s",
                     active > 0 ? "measured during a live stream"
                                : "measured while idle");
        else
            snprintf(rows[3].note, sizeof(rows[3].note),
                     "TCP handshake, measured now");
    }
}

/* ---------------------------------------------------------------- screens */

static void draw(tvui *u, const config *c, row rows[N_ROWS], int secs_left)
{
    tvui_clear(u);
    int y = tvui_header(u, "Homelab status");

    int lb = tvui_line_h(u->font_body);
    int ls = tvui_line_h(u->font_small);
    int row_h = lb + ls + ls / 2;

    /* Labels in a fixed left column so the values line up down the screen --
     * a ragged right edge is much harder to read at four metres. */
    int colw = 0;
    for (int i = 0; i < N_ROWS; i++) {
        int w = tvui_text_w(u, u->font_body, rows[i].label);
        if (w > colw) colw = w;
    }
    colw += u->w / 24;

    for (int i = 0; i < N_ROWS; i++) {
        tvui_text(u, u->font_body, u->safe, y, TV_DIM, "%s", rows[i].label);
        tvui_text(u, u->font_body, u->safe + colw, y,
                  rows[i].ok ? rows[i].tone : TV_BAD, "%s", rows[i].value);
        if (rows[i].note[0])
            tvui_text(u, u->font_small, u->safe + colw, y + lb,
                      TV_DIM, "%s", rows[i].note);
        y += row_h;
    }

    y += ls;
    tvui_text(u, u->font_small, u->safe, y, TV_DIM, "%s", c->prom);

    char hint[128];
    snprintf(hint, sizeof(hint),
             "A to refresh now    B to exit    next refresh in %ds", secs_left);
    tvui_footer(u, hint);
    tvui_present(u);
}

static void draw_busy(tvui *u, const char *what)
{
    tvui_clear(u);
    int y = tvui_header(u, "Homelab status");
    tvui_text(u, u->font_body, u->safe, y, TV_ACCENT, "%s...", what);
    tvui_present(u);
}

static void draw_fatal(tvui *u, const char *msg)
{
    tvui_clear(u);
    int y = tvui_header(u, "Homelab status");
    tvui_text(u, u->font_body, u->safe, y, TV_BAD, "Not configured");
    tvui_text(u, u->font_small, u->safe, y + tvui_line_h(u->font_body) * 3 / 2,
              TV_DIM, "%s", msg);
    tvui_footer(u, "Any button to exit");
    tvui_present(u);
}

/* ------------------------------------------------------------------- main */

int main(int argc, char *argv[])
{
    const char *shot = NULL;
    for (int i = 1; i < argc - 1; i++)
        if (!strcmp(argv[i], "--screenshot")) shot = argv[i + 1];

    tvui u;
    if (tvui_init(&u, "Homelab status") != 0)
        return 1;

    config c;
    char err[MAX_LINE];
    if (config_load(&c, err, sizeof(err)) != 0) {
        draw_fatal(&u, err);
        if (shot) {
            int rc = tvui_screenshot(&u, shot);
            tvui_quit(&u);
            return rc == 0 ? 0 : 1;
        }
        while (tvui_wait(&u, 200) == TV_NONE) { }
        tvui_quit(&u);
        return 1;
    }

    row rows[N_ROWS];
    draw_busy(&u, "Reading");
    gather(&c, rows);

    if (shot) {
        draw(&u, &c, rows, c.refresh);
        int rc = tvui_screenshot(&u, shot);
        tvui_quit(&u);
        return rc == 0 ? 0 : 1;
    }

    Uint32 last = SDL_GetTicks();
    for (;;) {
        int elapsed = (int)((SDL_GetTicks() - last) / 1000);
        int left = c.refresh - elapsed;
        if (left < 0) left = 0;

        draw(&u, &c, rows, left);

        tv_button b = tvui_wait(&u, 250);
        if (b == TV_BACK || b == TV_QUIT) break;

        if (b == TV_ACCEPT || left == 0) {
            draw_busy(&u, "Reading");
            gather(&c, rows);
            last = SDL_GetTicks();
        }
    }

    tvui_quit(&u);
    return 0;
}
