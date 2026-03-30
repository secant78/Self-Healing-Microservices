using Azure.Monitor.OpenTelemetry.AspNetCore;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using OrderService.Services;
using Polly;
using Polly.Extensions.Http;

var builder = WebApplication.CreateBuilder(args);

// ── OpenTelemetry ──────────────────────────────────────────────────────────────
var appInsightsConnectionString = builder.Configuration["APPLICATIONINSIGHTS_CONNECTION_STRING"]
    ?? Environment.GetEnvironmentVariable("APPLICATIONINSIGHTS_CONNECTION_STRING");

builder.Services.AddOpenTelemetry()
    .ConfigureResource(resource => resource
        .AddService(
            serviceName: "order-service",
            serviceVersion: "1.0.0"))
    .WithTracing(tracing =>
    {
        tracing
            .AddAspNetCoreInstrumentation(options =>
            {
                options.RecordException = true;
            })
            .AddHttpClientInstrumentation(options =>
            {
                options.RecordException = true;
            })
            .AddOtlpExporter(options =>
            {
                var otlpEndpoint = builder.Configuration["OTEL_EXPORTER_OTLP_ENDPOINT"]
                    ?? Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_ENDPOINT")
                    ?? "http://localhost:4317";
                options.Endpoint = new Uri(otlpEndpoint);
            });
    });

if (!string.IsNullOrWhiteSpace(appInsightsConnectionString))
{
    builder.Services.AddOpenTelemetry().UseAzureMonitor(options =>
    {
        options.ConnectionString = appInsightsConnectionString;
    });
}

// ── Polly Circuit Breaker Policy ───────────────────────────────────────────────
// Breaks after 5 consecutive failures; stays open for 30 seconds.
// A "failure" is any exception or a 5xx / 408 response.
var circuitBreakerPolicy = HttpPolicyExtensions
    .HandleTransientHttpError()
    .CircuitBreakerAsync(
        handledEventsAllowedBeforeBreaking: 5,
        durationOfBreak: TimeSpan.FromSeconds(30),
        onBreak: (outcome, breakDelay) =>
        {
            Console.WriteLine(
                $"[CircuitBreaker] Open for {breakDelay.TotalSeconds}s — reason: {outcome.Exception?.Message ?? outcome.Result?.StatusCode.ToString()}");
        },
        onReset: () => Console.WriteLine("[CircuitBreaker] Closed — resuming normal operation."),
        onHalfOpen: () => Console.WriteLine("[CircuitBreaker] Half-open — testing payment service."));

var retryPolicy = HttpPolicyExtensions
    .HandleTransientHttpError()
    .WaitAndRetryAsync(
        retryCount: 2,
        sleepDurationProvider: attempt => TimeSpan.FromMilliseconds(200 * attempt));

// ── HttpClient for Payment Service ────────────────────────────────────────────
var paymentServiceUrl = builder.Configuration["PAYMENT_SERVICE_URL"]
    ?? Environment.GetEnvironmentVariable("PAYMENT_SERVICE_URL")
    ?? "http://payment-service:8080";

builder.Services.AddHttpClient<PaymentClient>(client =>
{
    client.BaseAddress = new Uri(paymentServiceUrl);
    client.Timeout = TimeSpan.FromSeconds(15);
    client.DefaultRequestHeaders.Add("Accept", "application/json");
})
.AddPolicyHandler(retryPolicy)
.AddPolicyHandler(circuitBreakerPolicy);

// ── Controllers & API ─────────────────────────────────────────────────────────
builder.Services.AddControllers();
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen(options =>
{
    options.SwaggerDoc("v1", new Microsoft.OpenApi.Models.OpenApiInfo
    {
        Title = "Order Service API",
        Version = "v1",
        Description = "Receives and processes customer orders, orchestrating payment via the Payment Service."
    });
});

// ── Health Checks ─────────────────────────────────────────────────────────────
builder.Services.AddHealthChecks()
    .AddUrlGroup(
        new Uri($"{paymentServiceUrl}/health"),
        name: "payment-service",
        failureStatus: HealthStatus.Degraded,
        tags: new[] { "dependencies" });

// ─────────────────────────────────────────────────────────────────────────────
var app = builder.Build();

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI(options =>
    {
        options.SwaggerEndpoint("/swagger/v1/swagger.json", "Order Service v1");
    });
}

app.UseRouting();
app.MapControllers();
app.MapHealthChecks("/health");

app.Run();
