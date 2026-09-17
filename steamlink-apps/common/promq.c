#include "promq.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void promq_urlencode(const char *in, char *out, size_t outn)
{
    static const char *keep =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~";
    size_t o = 0;

    if (!outn) return;

    for (const char *p = in; *p && o + 4 < outn; p++) {
        if (strchr(keep, *p)) {
            out[o++] = *p;
        } else {
            snprintf(out + o, outn - o, "%%%02X", (unsigned char)*p);
            o += 3;
        }
    }
    out[o] = '\0';
}

/* Prometheus answers an instant query with
 *
 *   {"status":"success","data":{"resultType":"vector",
 *    "result":[{"metric":{...},"value":[<ts>,"<val>"]}]}}
 *
 * Carrying a JSON parser onto a 256MB box to read one float is not worth it,
 * so this looks for that one shape. It insists on "success" first, so an
 * error response can never be read as a measurement, and it distinguishes an
 * empty result -- a query that legitimately matched nothing, such as
 * count(up == 0) when everything is up -- from a failure to ask.
 */
promq_result promq_parse(const char *body, double *out)
{
    if (!body || !*body) return PROMQ_ERROR;

    if (!strstr(body, "\"status\":\"success\"")) return PROMQ_ERROR;

    const char *res = strstr(body, "\"result\":");
    if (res && !strncmp(res, "\"result\":[]", 11)) return PROMQ_EMPTY;

    const char *v = strstr(body, "\"value\":[");
    if (!v) return PROMQ_EMPTY;

    /* Step over the timestamp to the quoted value that follows it. */
    const char *comma = strchr(v, ',');
    if (!comma) return PROMQ_ERROR;
    const char *q = strchr(comma, '"');
    if (!q) return PROMQ_ERROR;
    q++;

    char num[64];
    size_t i = 0;
    while (*q && *q != '"' && i < sizeof(num) - 1) num[i++] = *q++;
    num[i] = '\0';
    if (!i) return PROMQ_ERROR;

    /* Prometheus sends NaN and +Inf as bare words in that string. strtod
     * would happily accept them, and a NaN rendered on screen as "nan%" is
     * worse than saying the value is unavailable. */
    if (strchr(num, 'n') || strchr(num, 'N') ||
        strchr(num, 'i') || strchr(num, 'I'))
        return PROMQ_ERROR;

    char *end = NULL;
    double d = strtod(num, &end);
    if (end == num || (end && *end != '\0')) return PROMQ_ERROR;

    *out = d;
    return PROMQ_OK;
}

promq_result promq_query(const char *base, const char *expr, int timeout,
                         double *out)
{
    char enc[1024], cmd[1600], body[8192];

    promq_urlencode(expr, enc, sizeof(enc));

    /* The expression is percent-encoded into the URL, so PromQL's braces,
     * quotes and equals signs never reach the shell. */
    snprintf(cmd, sizeof(cmd),
             "curl -sS -m %d '%s/api/v1/query?query=%s' 2>/dev/null",
             timeout, base, enc);

    FILE *p = popen(cmd, "r");
    if (!p) return PROMQ_ERROR;

    size_t n = fread(body, 1, sizeof(body) - 1, p);
    body[n] = '\0';
    int rc = pclose(p);

    if (rc != 0 || n == 0) return PROMQ_ERROR;

    return promq_parse(body, out);
}
