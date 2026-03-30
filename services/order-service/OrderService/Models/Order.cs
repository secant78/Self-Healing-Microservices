using System.ComponentModel.DataAnnotations;

namespace OrderService.Models;

/// <summary>
/// Incoming order request from the Frontend.
/// </summary>
public class OrderRequest
{
    [Required(ErrorMessage = "ProductId is required.")]
    [MinLength(1, ErrorMessage = "ProductId cannot be empty.")]
    public string ProductId { get; set; } = string.Empty;

    [Range(1, int.MaxValue, ErrorMessage = "Quantity must be greater than 0.")]
    public int Quantity { get; set; }

    [Required(ErrorMessage = "CustomerId is required.")]
    [MinLength(1, ErrorMessage = "CustomerId cannot be empty.")]
    public string CustomerId { get; set; } = string.Empty;
}

/// <summary>
/// Response returned to the caller after order processing.
/// </summary>
public class OrderResponse
{
    public Guid OrderId { get; set; }

    /// <summary>
    /// "Created", "Failed", or "ServiceUnavailable".
    /// </summary>
    public string Status { get; set; } = string.Empty;

    /// <summary>
    /// W3C TraceId from the active OpenTelemetry span, enabling end-to-end correlation.
    /// </summary>
    public string TraceId { get; set; } = string.Empty;

    public PaymentResult PaymentResult { get; set; } = new();

    public DateTime ProcessedAt { get; set; }
}

/// <summary>
/// Result returned by the Payment Service.
/// </summary>
public class PaymentResult
{
    public bool Success { get; set; }

    /// <summary>
    /// Present only when the payment succeeded.
    /// </summary>
    public string? TransactionId { get; set; }

    public string Message { get; set; } = string.Empty;
}
