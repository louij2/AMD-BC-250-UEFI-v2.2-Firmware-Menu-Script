/* promq - the smallest thing that can read one number out of Prometheus.
 *
 * Deliberately free of any SDL dependency, so it can be compiled and tested
 * on a build host without the Steam Link's hardware-coupled SDL2. The whole
 * point of this file is that the JSON handling is hand-rolled, and hand-rolled
 * parsing deserves a test.
 */
#ifndef PROMQ_H
#define PROMQ_H

#include <stddef.h>

typedef enum {
    PROMQ_OK = 0,        /* got a value */
    PROMQ_EMPTY,         /* the query ran and matched nothing */
    PROMQ_ERROR          /* could not ask, or the answer made no sense */
} promq_result;

/* Percent-encode an expression for a query string. */
void promq_urlencode(const char *in, char *out, size_t outn);

/* Pull the first sample's value out of an instant-query response body.
 * Exposed separately from the fetch so it can be tested against captured
 * bodies, including malformed ones. */
promq_result promq_parse(const char *body, double *out);

/* Run an instant query via curl. base is a Prometheus URL with no trailing
 * slash; timeout is in seconds. */
promq_result promq_query(const char *base, const char *expr, int timeout,
                         double *out);

#endif /* PROMQ_H */
