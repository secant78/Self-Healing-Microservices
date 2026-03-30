using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Moq;
using OrderService.Models;
using OrderService.Services;
using Polly.CircuitBreaker;
using Xunit;

namespace OrderService.Tests;

/// <summary>
/// Integration-style tests for <see cref="Controllers.OrderController"/>.
/// Uses <see cref="WebApplicationFactory{TEntryPoint}"/> so the full ASP.NET
/// Core pipeline (routing, model binding, validation) runs on every request.
/// The <see cref="PaymentClient"/> dependency is replaced with a Moq mock via
/// <c>ConfigureTestServices</c>.
/// </summary>
public class OrderControllerTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;

    public OrderControllerTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    /// <summary>
    /// Build an <see cref="HttpClient"/> whose DI container has the
    /// <see cref="PaymentClient"/> replaced by the supplied mock.
    /// </summary>
    private HttpClient CreateClientWithPaymentMock(Mock<PaymentClient> paymentClientMock)
    {
        var client = _factory.WithWebHostBuilder(builder =>
        {
            builder.ConfigureTestServices(services =>
            {
                // Remove the real PaymentClient registration (added via
                // AddHttpClient<PaymentClient>) and replace with our mock.
                var descriptor = services.SingleOrDefault(
                    d => d.ServiceType == typeof(PaymentClient));
                if (descriptor is not null)
                    services.Remove(descriptor);

                services.AddSingleton(paymentClientMock.Object);
            });
        }).CreateClient();

        return client;
    }

    private static StringContent Json(object payload) =>
        new StringContent(
            JsonSerializer.Serialize(payload),
            Encoding.UTF8,
            "application/json");

    // ── POST /order — success ─────────────────────────────────────────────────

    [Fact]
    public async Task CreateOrder_ValidRequest_Returns200WithOrderResponse()
    {
        // Arrange
        var paymentResult = new PaymentResult
        {
            Success = true,
            TransactionId = "txn-test-001",
            Message = "Payment processed successfully."
        };

        var mock = new Mock<PaymentClient>(
            Mock.Of<HttpClient>(),
            Mock.Of<Microsoft.Extensions.Logging.ILogger<PaymentClient>>());

        mock.Setup(c => c.ProcessPaymentAsync(It.IsAny<OrderRequest>()))
            .ReturnsAsync(paymentResult);

        var client = CreateClientWithPaymentMock(mock);

        var payload = new { productId = "prod-abc", quantity = 2, customerId = "cust-1" };

        // Act
        var response = await client.PostAsync("/order", Json(payload));

        // Assert
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var body = await response.Content.ReadFromJsonAsync<OrderResponse>(
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true });

        Assert.NotNull(body);
        Assert.NotEqual(Guid.Empty, body!.OrderId);
        Assert.Equal("Created", body.Status);
        Assert.NotNull(body.PaymentResult);
        Assert.True(body.PaymentResult.Success);
        Assert.Equal("txn-test-001", body.PaymentResult.TransactionId);
    }

    [Fact]
    public async Task CreateOrder_ValidRequest_PaymentClientCalledWithOrderDetails()
    {
        // Arrange
        var capturedRequest = default(OrderRequest);

        var paymentResult = new PaymentResult { Success = true, TransactionId = "txn-cap" };

        var mock = new Mock<PaymentClient>(
            Mock.Of<HttpClient>(),
            Mock.Of<Microsoft.Extensions.Logging.ILogger<PaymentClient>>());

        mock.Setup(c => c.ProcessPaymentAsync(It.IsAny<OrderRequest>()))
            .Callback<OrderRequest>(r => capturedRequest = r)
            .ReturnsAsync(paymentResult);

        var client = CreateClientWithPaymentMock(mock);
        var payload = new { productId = "prod-capture", quantity = 3, customerId = "cust-cap" };

        // Act
        await client.PostAsync("/order", Json(payload));

        // Assert — PaymentClient was called with the correct data
        mock.Verify(c => c.ProcessPaymentAsync(It.IsAny<OrderRequest>()), Times.Once);
        Assert.NotNull(capturedRequest);
        Assert.Equal("prod-capture", capturedRequest!.ProductId);
        Assert.Equal(3, capturedRequest.Quantity);
        Assert.Equal("cust-cap", capturedRequest.CustomerId);
    }

    [Fact]
    public async Task CreateOrder_PaymentFailed_Returns200WithFailedStatus()
    {
        // Arrange — payment client reports failure (not an exception)
        var paymentResult = new PaymentResult
        {
            Success = false,
            TransactionId = null,
            Message = "Payment declined (HTTP 500)."
        };

        var mock = new Mock<PaymentClient>(
            Mock.Of<HttpClient>(),
            Mock.Of<Microsoft.Extensions.Logging.ILogger<PaymentClient>>());

        mock.Setup(c => c.ProcessPaymentAsync(It.IsAny<OrderRequest>()))
            .ReturnsAsync(paymentResult);

        var client = CreateClientWithPaymentMock(mock);
        var payload = new { productId = "prod-fail", quantity = 1, customerId = "cust-fail" };

        // Act
        var response = await client.PostAsync("/order", Json(payload));

        // Assert — controller returns 200 with status="Failed"
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var body = await response.Content.ReadFromJsonAsync<OrderResponse>(
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true });

        Assert.NotNull(body);
        Assert.Equal("Failed", body!.Status);
        Assert.False(body.PaymentResult.Success);
    }

    // ── POST /order — validation failures ─────────────────────────────────────

    [Fact]
    public async Task CreateOrder_ZeroQuantity_Returns400()
    {
        // Arrange — no mock needed, validation fires before PaymentClient is called
        var client = _factory.CreateClient();
        var payload = new { productId = "prod-1", quantity = 0, customerId = "cust-1" };

        // Act
        var response = await client.PostAsync("/order", Json(payload));

        // Assert
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task CreateOrder_NegativeQuantity_Returns400()
    {
        var client = _factory.CreateClient();
        var payload = new { productId = "prod-1", quantity = -5, customerId = "cust-1" };

        var response = await client.PostAsync("/order", Json(payload));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task CreateOrder_EmptyProductId_Returns400()
    {
        var client = _factory.CreateClient();
        var payload = new { productId = "", quantity = 1, customerId = "cust-1" };

        var response = await client.PostAsync("/order", Json(payload));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task CreateOrder_MissingCustomerId_Returns400()
    {
        var client = _factory.CreateClient();
        // Send only productId and quantity — customerId is required
        var payload = new { productId = "prod-1", quantity = 1 };

        var response = await client.PostAsync("/order", Json(payload));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task CreateOrder_WhitespaceProductId_Returns400()
    {
        var client = _factory.CreateClient();
        var payload = new { productId = "   ", quantity = 1, customerId = "cust-1" };

        var response = await client.PostAsync("/order", Json(payload));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    // ── POST /order — circuit breaker ─────────────────────────────────────────

    [Fact]
    public async Task CreateOrder_PaymentClientThrowsBrokenCircuitException_Returns503()
    {
        // Arrange — simulate the Polly circuit breaker being open
        var mock = new Mock<PaymentClient>(
            Mock.Of<HttpClient>(),
            Mock.Of<Microsoft.Extensions.Logging.ILogger<PaymentClient>>());

        mock.Setup(c => c.ProcessPaymentAsync(It.IsAny<OrderRequest>()))
            .ThrowsAsync(new BrokenCircuitException("Circuit breaker is open."));

        var client = CreateClientWithPaymentMock(mock);
        var payload = new { productId = "prod-cb", quantity = 1, customerId = "cust-cb" };

        // Act
        var response = await client.PostAsync("/order", Json(payload));

        // Assert
        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
    }

    [Fact]
    public async Task CreateOrder_BrokenCircuitException_ResponseBodyContainsPaymentSystemBusy()
    {
        // Arrange
        var mock = new Mock<PaymentClient>(
            Mock.Of<HttpClient>(),
            Mock.Of<Microsoft.Extensions.Logging.ILogger<PaymentClient>>());

        mock.Setup(c => c.ProcessPaymentAsync(It.IsAny<OrderRequest>()))
            .ThrowsAsync(new BrokenCircuitException("Circuit open."));

        var client = CreateClientWithPaymentMock(mock);
        var payload = new { productId = "prod-busy", quantity = 1, customerId = "cust-busy" };

        // Act
        var response = await client.PostAsync("/order", Json(payload));
        var body = await response.Content.ReadAsStringAsync();

        // Assert — ProblemDetails title should say "Payment System Busy"
        Assert.Contains("Payment System Busy", body);
    }

    [Fact]
    public async Task CreateOrder_BrokenCircuitException_ResponseBodyContainsTraceId()
    {
        var mock = new Mock<PaymentClient>(
            Mock.Of<HttpClient>(),
            Mock.Of<Microsoft.Extensions.Logging.ILogger<PaymentClient>>());

        mock.Setup(c => c.ProcessPaymentAsync(It.IsAny<OrderRequest>()))
            .ThrowsAsync(new BrokenCircuitException("Circuit open."));

        var client = CreateClientWithPaymentMock(mock);
        var payload = new { productId = "prod-trace", quantity = 1, customerId = "cust-trace" };

        var response = await client.PostAsync("/order", Json(payload));
        var body = await response.Content.ReadAsStringAsync();

        // The ProblemDetails extensions include traceId
        Assert.Contains("traceId", body);
    }

    // ── GET /health ───────────────────────────────────────────────────────────

    [Fact]
    public async Task Health_Returns200()
    {
        var client = _factory.CreateClient();
        var response = await client.GetAsync("/health");
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task Health_ResponseContainsHealthyStatus()
    {
        var client = _factory.CreateClient();
        var response = await client.GetAsync("/health");
        var body = await response.Content.ReadAsStringAsync();
        Assert.Contains("Healthy", body);
    }
}
