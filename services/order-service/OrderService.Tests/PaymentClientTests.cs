using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using OrderService.Models;
using OrderService.Services;
using Xunit;

namespace OrderService.Tests;

/// <summary>
/// Unit tests for <see cref="PaymentClient"/>.
///
/// These tests do not use WebApplicationFactory — they construct the
/// <see cref="PaymentClient"/> directly using a hand-rolled
/// <see cref="MockHttpMessageHandler"/> so no external packages are needed
/// beyond what is already in the test project.
/// </summary>
public class PaymentClientTests
{
    // ── MockHttpMessageHandler ─────────────────────────────────────────────────

    /// <summary>
    /// A minimal <see cref="HttpMessageHandler"/> that returns a pre-configured
    /// <see cref="HttpResponseMessage"/> without touching the network.
    /// </summary>
    private sealed class MockHttpMessageHandler : HttpMessageHandler
    {
        private readonly Func<HttpRequestMessage, Task<HttpResponseMessage>> _handler;

        public MockHttpMessageHandler(Func<HttpRequestMessage, Task<HttpResponseMessage>> handler)
        {
            _handler = handler;
        }

        /// <summary>Convenience constructor for a synchronous response factory.</summary>
        public MockHttpMessageHandler(Func<HttpRequestMessage, HttpResponseMessage> handler)
            : this(req => Task.FromResult(handler(req))) { }

        /// <summary>Convenience constructor for a fixed response.</summary>
        public MockHttpMessageHandler(HttpResponseMessage response)
            : this(_ => Task.FromResult(response)) { }

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            if (cancellationToken.IsCancellationRequested)
                return Task.FromCanceled<HttpResponseMessage>(cancellationToken);

            return _handler(request);
        }
    }

    /// <summary>
    /// A handler that always throws <see cref="TaskCanceledException"/> to
    /// simulate a request timeout.
    /// </summary>
    private sealed class TimeoutHttpMessageHandler : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            // Mimic what HttpClient does on timeout: throw TaskCanceledException
            // with an inner TimeoutException.
            throw new TaskCanceledException(
                "A task was canceled.",
                new TimeoutException("The operation has timed out."));
        }
    }

    // ── Helpers ────────────────────────────────────────────────────────────────

    private static PaymentClient BuildClient(HttpMessageHandler handler, string baseUrl = "http://payment-service:8080")
    {
        var httpClient = new HttpClient(handler)
        {
            BaseAddress = new Uri(baseUrl)
        };

        return new PaymentClient(httpClient, NullLogger<PaymentClient>.Instance);
    }

    private static OrderRequest SampleOrder(string productId = "prod-1", int quantity = 2, string customerId = "cust-1")
        => new OrderRequest
        {
            ProductId = productId,
            Quantity = quantity,
            CustomerId = customerId
        };

    private static HttpResponseMessage OkPaymentResponse(string transactionId = "txn-unit-001")
    {
        var result = new PaymentResult
        {
            Success = true,
            TransactionId = transactionId,
            Message = "Payment successful."
        };

        var json = JsonSerializer.Serialize(result, new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase
        });

        return new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(json, Encoding.UTF8, "application/json")
        };
    }

    private static HttpResponseMessage ErrorResponse(HttpStatusCode statusCode, string body = "Internal Server Error")
    {
        return new HttpResponseMessage(statusCode)
        {
            Content = new StringContent(body, Encoding.UTF8, "application/json")
        };
    }

    // ── ProcessPaymentAsync — HTTP 200 success ─────────────────────────────────

    [Fact]
    public async Task ProcessPaymentAsync_Http200_ReturnsSuccessResult()
    {
        // Arrange
        var client = BuildClient(new MockHttpMessageHandler(OkPaymentResponse("txn-200")));

        // Act
        var result = await client.ProcessPaymentAsync(SampleOrder());

        // Assert
        Assert.True(result.Success);
        Assert.Equal("txn-200", result.TransactionId);
    }

    [Fact]
    public async Task ProcessPaymentAsync_Http200_PostsToPaymentEndpoint()
    {
        // Arrange — capture the request URL
        HttpRequestMessage? capturedRequest = null;
        var handler = new MockHttpMessageHandler(req =>
        {
            capturedRequest = req;
            return OkPaymentResponse();
        });

        var client = BuildClient(handler);

        // Act
        await client.ProcessPaymentAsync(SampleOrder());

        // Assert
        Assert.NotNull(capturedRequest);
        Assert.Equal(HttpMethod.Post, capturedRequest!.Method);
        Assert.EndsWith("/payment", capturedRequest.RequestUri!.ToString());
    }

    [Fact]
    public async Task ProcessPaymentAsync_Http200_ResultHasNonNullTransactionId()
    {
        var client = BuildClient(new MockHttpMessageHandler(OkPaymentResponse("txn-nonnull")));

        var result = await client.ProcessPaymentAsync(SampleOrder());

        Assert.NotNull(result.TransactionId);
        Assert.NotEmpty(result.TransactionId!);
    }

    // ── ProcessPaymentAsync — HTTP 500 failure ─────────────────────────────────

    [Fact]
    public async Task ProcessPaymentAsync_Http500_ReturnsFailureResult()
    {
        // Arrange
        var client = BuildClient(new MockHttpMessageHandler(ErrorResponse(HttpStatusCode.InternalServerError)));

        // Act
        var result = await client.ProcessPaymentAsync(SampleOrder());

        // Assert
        Assert.False(result.Success);
        Assert.Null(result.TransactionId);
    }

    [Fact]
    public async Task ProcessPaymentAsync_Http500_MessageContainsStatusCode()
    {
        var client = BuildClient(new MockHttpMessageHandler(ErrorResponse(HttpStatusCode.InternalServerError)));

        var result = await client.ProcessPaymentAsync(SampleOrder());

        Assert.Contains("500", result.Message);
    }

    [Fact]
    public async Task ProcessPaymentAsync_Http503_ReturnsFailureResult()
    {
        var client = BuildClient(new MockHttpMessageHandler(ErrorResponse(HttpStatusCode.ServiceUnavailable)));

        var result = await client.ProcessPaymentAsync(SampleOrder());

        Assert.False(result.Success);
        Assert.Null(result.TransactionId);
    }

    [Fact]
    public async Task ProcessPaymentAsync_Http400_ReturnsFailureResult()
    {
        var client = BuildClient(new MockHttpMessageHandler(ErrorResponse(HttpStatusCode.BadRequest)));

        var result = await client.ProcessPaymentAsync(SampleOrder());

        Assert.False(result.Success);
    }

    // ── ProcessPaymentAsync — timeout ─────────────────────────────────────────

    [Fact]
    public async Task ProcessPaymentAsync_Timeout_ReturnsFailureResult()
    {
        // Arrange
        var client = BuildClient(new TimeoutHttpMessageHandler());

        // Act
        var result = await client.ProcessPaymentAsync(SampleOrder());

        // Assert — TimeoutException is caught and surfaced as a failure result
        Assert.False(result.Success);
        Assert.Null(result.TransactionId);
    }

    [Fact]
    public async Task ProcessPaymentAsync_Timeout_MessageMentionsTimeout()
    {
        var client = BuildClient(new TimeoutHttpMessageHandler());

        var result = await client.ProcessPaymentAsync(SampleOrder());

        Assert.Contains("timed out", result.Message, StringComparison.OrdinalIgnoreCase);
    }

    // ── ProcessPaymentAsync — malformed JSON response ─────────────────────────

    [Fact]
    public async Task ProcessPaymentAsync_MalformedJson_ReturnsFailureResult()
    {
        // Arrange — server returns 200 but with invalid JSON
        var badJsonResponse = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent("this is not json", Encoding.UTF8, "application/json")
        };

        var client = BuildClient(new MockHttpMessageHandler(badJsonResponse));

        // Act
        var result = await client.ProcessPaymentAsync(SampleOrder());

        // Assert — JsonException is caught; client returns a failure result
        Assert.False(result.Success);
    }

    // ── ProcessPaymentAsync — empty body ──────────────────────────────────────

    [Fact]
    public async Task ProcessPaymentAsync_EmptyResponseBody_ReturnsFailureResult()
    {
        // Arrange — server returns 200 with an empty body
        var emptyResponse = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent("null", Encoding.UTF8, "application/json")
        };

        var client = BuildClient(new MockHttpMessageHandler(emptyResponse));

        // Act
        var result = await client.ProcessPaymentAsync(SampleOrder());

        // Assert — null deserialization is treated as failure
        Assert.False(result.Success);
    }

    // ── ProcessPaymentAsync — request payload passthrough ─────────────────────

    [Fact]
    public async Task ProcessPaymentAsync_SendsProductIdInPayload()
    {
        // Arrange
        string? capturedBody = null;
        var handler = new MockHttpMessageHandler(async req =>
        {
            capturedBody = await req.Content!.ReadAsStringAsync();
            return OkPaymentResponse();
        });

        var client = BuildClient(handler);

        // Act
        await client.ProcessPaymentAsync(SampleOrder(productId: "prod-unit-99"));

        // Assert — productId is present in the JSON body sent to the payment service
        Assert.NotNull(capturedBody);
        Assert.Contains("prod-unit-99", capturedBody!);
    }
}
