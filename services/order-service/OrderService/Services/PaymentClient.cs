using System.Diagnostics;
using System.Net.Http.Json;
using System.Text.Json;
using OrderService.Models;
using Polly.CircuitBreaker;

namespace OrderService.Services;

/// <summary>
/// Typed HTTP client that calls the Payment Service.
/// Polly circuit breaker and retry policies are applied in Program.cs via
/// IHttpClientFactory, so this class contains only the call logic.
/// </summary>
public class PaymentClient
{
    private readonly HttpClient _httpClient;
    private readonly ILogger<PaymentClient> _logger;

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true
    };

    public PaymentClient(HttpClient httpClient, ILogger<PaymentClient> logger)
    {
        _httpClient = httpClient;
        _logger = logger;
    }

    /// <summary>
    /// Sends a payment request to the Payment Service.
    /// Returns a <see cref="PaymentResult"/> in all cases; exceptions are caught
    /// and surfaced as failed results so the controller can decide on the HTTP status.
    /// Rethrows <see cref="BrokenCircuitException"/> so the controller can return 503.
    /// </summary>
    public async Task<PaymentResult> ProcessPaymentAsync(OrderRequest order)
    {
        var traceId = Activity.Current?.TraceId.ToString() ?? "none";
        var spanId = Activity.Current?.SpanId.ToString() ?? "none";

        _logger.LogInformation(
            "Sending payment request for customer {CustomerId}, product {ProductId}, qty {Quantity}. TraceId={TraceId} SpanId={SpanId}",
            order.CustomerId, order.ProductId, order.Quantity, traceId, spanId);

        var payload = new
        {
            productId = order.ProductId,
            quantity = order.Quantity,
            customerId = order.CustomerId
        };

        HttpResponseMessage response;
        try
        {
            // The HttpClient has W3C trace-context propagation enabled automatically
            // by OpenTelemetry.Instrumentation.Http, so traceparent/tracestate headers
            // are injected into every outbound request.
            response = await _httpClient.PostAsJsonAsync("/payment", payload);
        }
        catch (BrokenCircuitException ex)
        {
            _logger.LogWarning(
                ex,
                "Circuit breaker is open — payment service unavailable. TraceId={TraceId}",
                traceId);
            // Re-throw so the controller can return 503 Service Unavailable.
            throw;
        }
        catch (TaskCanceledException ex) when (ex.InnerException is TimeoutException || ex.CancellationToken == default)
        {
            _logger.LogError(
                ex,
                "Payment service request timed out. TraceId={TraceId}",
                traceId);
            return new PaymentResult
            {
                Success = false,
                TransactionId = null,
                Message = "Payment service request timed out."
            };
        }
        catch (HttpRequestException ex)
        {
            _logger.LogError(
                ex,
                "HTTP error communicating with payment service. TraceId={TraceId}",
                traceId);
            return new PaymentResult
            {
                Success = false,
                TransactionId = null,
                Message = $"Payment service communication error: {ex.Message}"
            };
        }

        _logger.LogInformation(
            "Payment service responded with {StatusCode}. TraceId={TraceId}",
            (int)response.StatusCode, traceId);

        if (!response.IsSuccessStatusCode)
        {
            var errorBody = await TryReadBodyAsync(response);
            _logger.LogWarning(
                "Payment service returned non-success status {StatusCode}. Body={Body}. TraceId={TraceId}",
                (int)response.StatusCode, errorBody, traceId);
            return new PaymentResult
            {
                Success = false,
                TransactionId = null,
                Message = $"Payment declined (HTTP {(int)response.StatusCode}): {errorBody}"
            };
        }

        PaymentResult? result;
        try
        {
            result = await response.Content.ReadFromJsonAsync<PaymentResult>(JsonOptions);
        }
        catch (JsonException ex)
        {
            _logger.LogError(
                ex,
                "Failed to deserialize payment service response. TraceId={TraceId}",
                traceId);
            return new PaymentResult
            {
                Success = false,
                TransactionId = null,
                Message = "Invalid response received from payment service."
            };
        }

        if (result is null)
        {
            return new PaymentResult
            {
                Success = false,
                TransactionId = null,
                Message = "Empty response received from payment service."
            };
        }

        _logger.LogInformation(
            "Payment processed — Success={Success} TransactionId={TransactionId} Message={Message}. TraceId={TraceId}",
            result.Success, result.TransactionId, result.Message, traceId);

        return result;
    }

    private static async Task<string> TryReadBodyAsync(HttpResponseMessage response)
    {
        try
        {
            return await response.Content.ReadAsStringAsync();
        }
        catch
        {
            return "(unable to read body)";
        }
    }
}
