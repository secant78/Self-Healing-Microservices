using System.Diagnostics;
using Microsoft.AspNetCore.Mvc;
using OrderService.Models;
using OrderService.Services;
using Polly.CircuitBreaker;

namespace OrderService.Controllers;

[ApiController]
[Route("")]
public class OrderController : ControllerBase
{
    private readonly PaymentClient _paymentClient;
    private readonly ILogger<OrderController> _logger;

    public OrderController(PaymentClient paymentClient, ILogger<OrderController> logger)
    {
        _paymentClient = paymentClient;
        _logger = logger;
    }

    /// <summary>
    /// Submit a new order. Validates the request, calls the Payment Service,
    /// and returns a full <see cref="OrderResponse"/> with distributed trace correlation.
    /// </summary>
    /// <response code="200">Order accepted and payment processed.</response>
    /// <response code="400">Validation failed — see the error details.</response>
    /// <response code="503">Payment system is unavailable (circuit breaker open).</response>
    [HttpPost("order")]
    [ProducesResponseType(typeof(OrderResponse), StatusCodes.Status200OK)]
    [ProducesResponseType(typeof(ValidationProblemDetails), StatusCodes.Status400BadRequest)]
    [ProducesResponseType(typeof(ProblemDetails), StatusCodes.Status503ServiceUnavailable)]
    public async Task<IActionResult> CreateOrder([FromBody] OrderRequest request)
    {
        // ── Manual validation in addition to [ApiController] model validation ──
        var validationErrors = ValidateOrder(request);
        if (validationErrors.Count > 0)
        {
            _logger.LogWarning(
                "Order validation failed for customer {CustomerId}: {Errors}",
                request.CustomerId, string.Join("; ", validationErrors));

            return ValidationProblem(new ValidationProblemDetails(
                validationErrors.ToDictionary(e => e, e => new[] { e }))
            {
                Title = "Order validation failed.",
                Status = StatusCodes.Status400BadRequest
            });
        }

        var orderId = Guid.NewGuid();
        var traceId = Activity.Current?.TraceId.ToString() ?? string.Empty;

        _logger.LogInformation(
            "Processing order {OrderId} for customer {CustomerId}, product {ProductId}, qty {Quantity}. TraceId={TraceId}",
            orderId, request.CustomerId, request.ProductId, request.Quantity, traceId);

        // Annotate the current span with order metadata for richer traces.
        Activity.Current?.SetTag("order.id", orderId.ToString());
        Activity.Current?.SetTag("order.customer_id", request.CustomerId);
        Activity.Current?.SetTag("order.product_id", request.ProductId);
        Activity.Current?.SetTag("order.quantity", request.Quantity);

        PaymentResult paymentResult;
        try
        {
            paymentResult = await _paymentClient.ProcessPaymentAsync(request);
        }
        catch (BrokenCircuitException ex)
        {
            _logger.LogWarning(
                ex,
                "Payment circuit breaker is open for order {OrderId}. TraceId={TraceId}",
                orderId, traceId);

            Activity.Current?.SetTag("order.status", "ServiceUnavailable");
            Activity.Current?.SetTag("circuit_breaker.open", true);

            return StatusCode(StatusCodes.Status503ServiceUnavailable, new ProblemDetails
            {
                Title = "Payment System Busy",
                Detail = "The payment system is temporarily unavailable. Please retry in a moment.",
                Status = StatusCodes.Status503ServiceUnavailable,
                Extensions = { ["traceId"] = traceId, ["orderId"] = orderId }
            });
        }

        var status = paymentResult.Success ? "Created" : "Failed";
        Activity.Current?.SetTag("order.status", status);
        Activity.Current?.SetTag("payment.success", paymentResult.Success);
        Activity.Current?.SetTag("payment.transaction_id", paymentResult.TransactionId ?? "none");

        _logger.LogInformation(
            "Order {OrderId} result: Status={Status} PaymentSuccess={PaymentSuccess} TraceId={TraceId}",
            orderId, status, paymentResult.Success, traceId);

        var response = new OrderResponse
        {
            OrderId = orderId,
            Status = status,
            TraceId = traceId,
            PaymentResult = paymentResult,
            ProcessedAt = DateTime.UtcNow
        };

        return Ok(response);
    }

    /// <summary>
    /// Liveness/readiness probe. Returns 200 OK when the service is running.
    /// Full dependency health is available at /health (registered via MapHealthChecks).
    /// </summary>
    [HttpGet("health")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    public IActionResult Health()
    {
        return Ok(new
        {
            status = "Healthy",
            service = "order-service",
            timestamp = DateTime.UtcNow
        });
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private static List<string> ValidateOrder(OrderRequest request)
    {
        var errors = new List<string>();

        if (string.IsNullOrWhiteSpace(request.ProductId))
            errors.Add("ProductId must not be empty.");

        if (request.Quantity <= 0)
            errors.Add($"Quantity must be greater than 0 (received {request.Quantity}).");

        if (string.IsNullOrWhiteSpace(request.CustomerId))
            errors.Add("CustomerId must not be empty.");

        return errors;
    }
}
