/* Tests for promq. Builds and runs on the x86 build host -- no SDL, no
 * Steam Link -- because the Steam Link's SDL2 talks to Marvell hardware at
 * init and cannot run under qemu.
 *
 *   cc -O2 -Wall -Wextra -o promq_test promq_test.c promq.c && ./promq_test
 *
 * Pass a Prometheus URL as argv[1] to additionally run the real queries the
 * status app uses against a live server.
 */
#include "promq.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

static int failures = 0;
static int checks = 0;

static void check(int cond, const char *what)
{
    checks++;
    if (!cond) {
        failures++;
        printf("  FAIL  %s\n", what);
    } else {
        printf("  ok    %s\n", what);
    }
}

static void expect_parse(const char *name, const char *body,
                         promq_result want, double want_val)
{
    double got = -12345;
    promq_result r = promq_parse(body, &got);
    if (r != want) {
        checks++; failures++;
        printf("  FAIL  %s: result %d, wanted %d\n", name, r, want);
        return;
    }
    if (want == PROMQ_OK && fabs(got - want_val) > 1e-9) {
        checks++; failures++;
        printf("  FAIL  %s: value %g, wanted %g\n", name, got, want_val);
        return;
    }
    checks++;
    printf("  ok    %s\n", name);
}

int main(int argc, char *argv[])
{
    printf("urlencode\n");
    {
        char out[256];
        promq_urlencode("sum(up)", out, sizeof(out));
        check(!strcmp(out, "sum%28up%29"), "parentheses are escaped");

        promq_urlencode("node_cpu_seconds_total{job=\"bc250\"}", out, sizeof(out));
        check(!strcmp(out,
              "node_cpu_seconds_total%7Bjob%3D%22bc250%22%7D"),
              "braces, equals and quotes are escaped");

        promq_urlencode("a-b_c.d~e", out, sizeof(out));
        check(!strcmp(out, "a-b_c.d~e"), "unreserved characters pass through");

        /* A buffer too small must still terminate rather than run off. */
        char tiny[8];
        promq_urlencode("sum(up)", tiny, sizeof(tiny));
        check(strlen(tiny) < sizeof(tiny), "a short buffer stays terminated");
    }

    printf("parse: the good case\n");
    expect_parse("a plain vector sample",
        "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\","
        "\"result\":[{\"metric\":{},\"value\":[1789685694.44,\"191\"]}]}}",
        PROMQ_OK, 191);

    expect_parse("a fractional value",
        "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\","
        "\"result\":[{\"metric\":{\"host\":\"unraid\"},"
        "\"value\":[1789685684.886,\"0.05309254399969992\"]}]}}",
        PROMQ_OK, 0.05309254399969992);

    expect_parse("labels containing the word value",
        "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\","
        "\"result\":[{\"metric\":{\"name\":\"value\"},"
        "\"value\":[1789685684.0,\"7\"]}]}}",
        PROMQ_OK, 7);

    printf("parse: nothing matched\n");
    expect_parse("an empty result set",
        "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\","
        "\"result\":[]}}",
        PROMQ_EMPTY, 0);

    printf("parse: things that must not be read as a measurement\n");
    expect_parse("an error response",
        "{\"status\":\"error\",\"errorType\":\"bad_data\","
        "\"error\":\"parse error\"}",
        PROMQ_ERROR, 0);

    expect_parse("an empty body", "", PROMQ_ERROR, 0);

    expect_parse("truncated JSON",
        "{\"status\":\"success\",\"data\":{\"result\":[{\"value\":[178968",
        PROMQ_ERROR, 0);

    expect_parse("NaN",
        "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\","
        "\"result\":[{\"metric\":{},\"value\":[1789685694.44,\"NaN\"]}]}}",
        PROMQ_ERROR, 0);

    expect_parse("+Inf",
        "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\","
        "\"result\":[{\"metric\":{},\"value\":[1789685694.44,\"+Inf\"]}]}}",
        PROMQ_ERROR, 0);

    expect_parse("a value with trailing junk",
        "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\","
        "\"result\":[{\"metric\":{},\"value\":[1789685694.44,\"12abc\"]}]}}",
        PROMQ_ERROR, 0);

    /* An HTML error page from a proxy in front of Prometheus is a realistic
     * failure and must not parse. */
    expect_parse("an HTML error page",
        "<html><head><title>502 Bad Gateway</title></head></html>",
        PROMQ_ERROR, 0);

    if (argc > 1) {
        printf("live queries against %s\n", argv[1]);
        struct { const char *expr; int allow_empty; } qs[] = {
            { "sum(up)", 0 },
            { "count(up)", 0 },
            { "count(up == 0)", 1 },
            { "count(count by (cpu) (node_cpu_seconds_total{job=\"bc250\"}))", 0 },
            { "lancache_cache_hit_ratio", 0 },
            { "steamlink_streaming_active", 0 },
        };
        for (unsigned i = 0; i < sizeof(qs) / sizeof(qs[0]); i++) {
            double v = 0;
            promq_result r = promq_query(argv[1], qs[i].expr, 6, &v);
            char label[256];
            snprintf(label, sizeof(label), "%s", qs[i].expr);
            if (r == PROMQ_OK) {
                printf("  ok    %-70s = %g\n", label, v);
                checks++;
            } else if (r == PROMQ_EMPTY && qs[i].allow_empty) {
                printf("  ok    %-70s = (nothing matched, allowed)\n", label);
                checks++;
            } else {
                printf("  FAIL  %-70s -> result %d\n", label, r);
                checks++; failures++;
            }
        }
    }

    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
