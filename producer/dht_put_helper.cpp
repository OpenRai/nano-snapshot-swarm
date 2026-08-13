// Publish one BEP 46 mutable item with an explicitly monotonic sequence.
//
// libtorrent's Python dht_put_mutable_item() convenience binding derives its
// sequence from one lookup traversal. A multi-path lookup can see a lower final
// response than another valid response, causing it to sign a stale sequence.
// This helper uses session::dht_put_item(), whose callback lets us choose the
// new sequence after collecting every result through the authoritative marker.
// See https://www.libtorrent.org/single-page-ref.html#dht_put_item and
// https://www.libtorrent.org/reference-Alerts.html#dht_mutable_item_alert.

#include <array>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#include <openssl/evp.h>

#include "libtorrent/alert_types.hpp"
#include "libtorrent/bencode.hpp"
#include "libtorrent/kademlia/ed25519.hpp"
#include "libtorrent/kademlia/item.hpp"
#include "libtorrent/session.hpp"
#include "libtorrent/session_params.hpp"
#include "libtorrent/span.hpp"

namespace lt = libtorrent;
namespace dht = libtorrent::dht;

namespace {

bool decode_hex(std::string const& input, lt::span<char> output) {
    auto const output_size = static_cast<std::size_t>(output.size());
    if (input.size() != output_size * 2) return false;
    auto decode_nibble = [](char value) -> int {
        if (value >= '0' && value <= '9') return value - '0';
        if (value >= 'a' && value <= 'f') return value - 'a' + 10;
        if (value >= 'A' && value <= 'F') return value - 'A' + 10;
        return -1;
    };
    for (std::size_t index = 0; index < output_size; ++index) {
        int const high = decode_nibble(input[index * 2]);
        int const low = decode_nibble(input[index * 2 + 1]);
        if (high < 0 || low < 0) return false;
        output[index] = static_cast<char>((high << 4) | low);
    }
    return true;
}

bool wait_for_bootstrap(lt::session& session, std::chrono::seconds timeout) {
    auto const deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        session.wait_for_alert(std::chrono::seconds(1));
        std::vector<lt::alert*> alerts;
        session.pop_alerts(&alerts);
        for (lt::alert const* alert : alerts) {
            if (lt::alert_cast<lt::dht_bootstrap_alert>(alert) != nullptr) return true;
        }
    }
    return false;
}

bool verify_mutable_item(
    lt::entry const& item,
    std::string const& salt,
    std::int64_t sequence,
    dht::public_key const& public_key,
    std::array<char, 64> const& signature
) {
    std::vector<char> encoded_value;
    lt::bencode(std::back_inserter(encoded_value), item);
    std::string message;
    if (!salt.empty()) {
        message += "4:salt" + std::to_string(salt.size()) + ":" + salt;
    }
    message += "3:seqi" + std::to_string(sequence) + "e1:v";
    message.append(encoded_value.data(), encoded_value.size());

    EVP_PKEY* key = EVP_PKEY_new_raw_public_key(
        EVP_PKEY_ED25519,
        nullptr,
        reinterpret_cast<unsigned char const*>(public_key.bytes.data()),
        public_key.bytes.size()
    );
    if (key == nullptr) return false;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) {
        EVP_PKEY_free(key);
        return false;
    }
    int const initialized = EVP_DigestVerifyInit(context, nullptr, nullptr, nullptr, key);
    int const verified = initialized == 1
        ? EVP_DigestVerify(
            context,
            reinterpret_cast<unsigned char const*>(signature.data()),
            signature.size(),
            reinterpret_cast<unsigned char const*>(message.data()),
            message.size()
        )
        : 0;
    EVP_MD_CTX_free(context);
    EVP_PKEY_free(key);
    return verified == 1;
}

std::int64_t highest_verified_sequence(
    lt::session& session,
    dht::public_key const& public_key,
    std::string const& salt,
    std::chrono::seconds timeout
) {
    session.dht_get_item(public_key.bytes, salt);
    auto const deadline = std::chrono::steady_clock::now() + timeout;
    std::int64_t highest = 0;
    bool authoritative = false;

    while (!authoritative && std::chrono::steady_clock::now() < deadline) {
        session.wait_for_alert(std::chrono::seconds(1));
        std::vector<lt::alert*> alerts;
        session.pop_alerts(&alerts);
        for (lt::alert const* alert : alerts) {
            auto const* item = lt::alert_cast<lt::dht_mutable_item_alert>(alert);
            if (item == nullptr || item->salt != salt || item->key != public_key.bytes) continue;

            if (verify_mutable_item(
                    item->item, salt, item->seq, public_key, item->signature
                )) {
                highest = std::max(highest, item->seq);
            }
            authoritative = authoritative || item->authoritative;
        }
    }

    if (!authoritative) throw std::runtime_error("DHT lookup did not reach authoritative completion");
    return highest;
}

[[noreturn]] void usage() {
    std::cerr << "Usage: nano-dht-put --info-hash <64 hex> --salt <salt>\n";
    std::exit(2);
}

}  // namespace

int main(int argc, char* argv[]) {
    std::string info_hash;
    std::string salt;
    for (int index = 1; index < argc; ++index) {
        std::string const argument = argv[index];
        if (argument == "--info-hash" && index + 1 < argc) info_hash = argv[++index];
        else if (argument == "--salt" && index + 1 < argc) salt = argv[++index];
        else usage();
    }
    if (info_hash.size() != 64 || salt.empty() || salt.size() > 64) usage();

    char const* private_key_env = std::getenv("DHT_PRIVATE_KEY");
    if (private_key_env == nullptr) {
        std::cerr << "DHT_PRIVATE_KEY is not set\n";
        return 2;
    }
    std::string private_key_hex = private_key_env;
    if (private_key_hex.size() == 128) private_key_hex.resize(64);
    std::array<char, 32> seed{};
    if (!decode_hex(private_key_hex, seed)) {
        std::cerr << "DHT_PRIVATE_KEY must be 32- or 64-byte hex\n";
        return 2;
    }
    std::array<char, 32> value{};
    if (!decode_hex(info_hash, value)) usage();

    dht::public_key public_key;
    dht::secret_key private_key;
    std::tie(public_key, private_key) = dht::ed25519_create_keypair(seed);

    lt::settings_pack settings;
    settings.set_bool(lt::settings_pack::enable_dht, true);
    settings.set_int(
        lt::settings_pack::alert_mask,
        lt::alert_category::dht | lt::alert_category::error
    );
    settings.set_str(lt::settings_pack::listen_interfaces, "0.0.0.0:0,[::]:0");
    lt::session session(settings);

    try {
        if (!wait_for_bootstrap(session, std::chrono::seconds(120))) {
            throw std::runtime_error("DHT bootstrap timed out");
        }
        std::int64_t const observed_sequence = highest_verified_sequence(
            session, public_key, salt, std::chrono::seconds(120)
        );
        std::int64_t const minimum_sequence = observed_sequence + 1;
        session.dht_put_item(
            public_key.bytes,
            [public_key, private_key, value, minimum_sequence](
                lt::entry& entry,
                std::array<char, 64>& signature,
                std::int64_t& sequence,
                std::string const& callback_salt
            ) {
                sequence = std::max(sequence + 1, minimum_sequence);
                entry = std::string(value.data(), value.size());
                std::vector<char> encoded_value;
                lt::bencode(std::back_inserter(encoded_value), entry);
                signature = dht::sign_mutable_item(
                    encoded_value,
                    callback_salt,
                    dht::sequence_number(sequence),
                    public_key,
                    private_key
                ).bytes;
            },
            salt
        );

        auto const deadline = std::chrono::steady_clock::now() + std::chrono::seconds(120);
        while (std::chrono::steady_clock::now() < deadline) {
            session.wait_for_alert(std::chrono::seconds(1));
            std::vector<lt::alert*> alerts;
            session.pop_alerts(&alerts);
            for (lt::alert const* alert : alerts) {
                auto const* put = lt::alert_cast<lt::dht_put_alert>(alert);
                if (put == nullptr || put->salt != salt) continue;
                std::cout << "{\"sequence\":" << put->seq
                          << ",\"direct_acknowledgements\":" << put->num_success
                          << ",\"observed_sequence\":" << observed_sequence << "}\n";
                return 0;
            }
        }
        throw std::runtime_error("DHT put timed out");
    } catch (std::exception const& error) {
        std::cerr << error.what() << "\n";
        return 1;
    }
}
