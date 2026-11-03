#pragma once
#include <gmpxx.h>
#include <algorithm>
#include <cstddef>
#include <deque>
#include <map>
#include <optional>
#include <set>
#include <vector>

namespace nia::affine {
using Int = mpz_class;
enum class Status { ok, unsupported, budget };
struct Limits {
  std::size_t members = 32, coefficient_bits = 4096, input_bits = 4096;
  std::size_t intermediate_bits = 16384, work = 8192;
  unsigned degree = 2, exponent = 64;
};
struct Domain {
  std::optional<Int> lower, upper;
  std::optional<std::vector<Int>> values;
};
struct Row {
  std::size_t first, second;
  Int first_coefficient, second_coefficient, constant;
  std::size_t source_id;
};
struct Member { std::size_t variable; Int a, b; };
struct View {
  std::size_t root;
  std::vector<Member> members;
  std::vector<std::size_t> selected_rows;
  Domain domain;
};
struct BuildResult {
  std::vector<View> views;
  std::size_t components = 0, skip_size = 0, skip_no_root = 0;
  std::size_t skip_bits = 0, skip_domain = 0, skip_unsupported = 0;
};
struct Factor { Int a, b; unsigned exponent; };
struct PolyResult { Status status = Status::unsupported; std::vector<Int> coefficients; };
struct DomainResult { Status status = Status::unsupported; Domain domain; };
inline std::size_t bits(const Int& value) {
  return value == 0 ? 0 : mpz_sizeinbase(value.get_mpz_t(), 2);
}
inline bool product_fits(const Int& left, const Int& right, std::size_t cap) {
  if (left == 0 || right == 0) return true;
  const auto lb = bits(left), rb = bits(right);
  return lb <= cap && rb <= cap - lb;
}
inline bool sum_fits(const Int& left, const Int& right, std::size_t cap) {
  if (left == 0) return bits(right) <= cap;
  if (right == 0) return bits(left) <= cap;
  return std::max(bits(left), bits(right)) < cap;
}
inline bool contains(const Domain& domain, const Int& value) {
  if ((domain.lower && value < *domain.lower) || (domain.upper && value > *domain.upper)) return false;
  return !domain.values || std::find(domain.values->begin(), domain.values->end(), value) != domain.values->end();
}
inline std::optional<Int> project(const Domain& domain, const Int& target) {
  if (domain.lower && domain.upper && *domain.lower > *domain.upper) return std::nullopt;
  if (domain.values) {
    std::optional<Int> selected;
    Int distance;
    for (const Int& value : *domain.values) if (contains(domain, value)) {
      Int candidate_distance = value - target;
      if (candidate_distance < 0) candidate_distance = -candidate_distance;
      if (!selected || candidate_distance < distance || (candidate_distance == distance && value < *selected)) {
        selected = value; distance = candidate_distance;
      }
    }
    return selected;
  }
  Int selected = target;
  if (domain.lower && selected < *domain.lower) selected = *domain.lower;
  if (domain.upper && selected > *domain.upper) selected = *domain.upper;
  return contains(domain, selected) ? std::optional<Int>(selected) : std::nullopt;
}
inline std::optional<Int> inverse(const Member& member, const Int& target) {
  if (member.a == 0) return std::nullopt;
  Int numerator = target - member.b;
  if (!mpz_divisible_p(numerator.get_mpz_t(), member.a.get_mpz_t())) return std::nullopt;
  return Int(numerator / member.a);
}
inline Int floor_div(const Int& numerator, const Int& denominator) {
  Int result; mpz_fdiv_q(result.get_mpz_t(), numerator.get_mpz_t(), denominator.get_mpz_t()); return result;
}
inline Int ceil_div(const Int& numerator, const Int& denominator) {
  Int result; mpz_cdiv_q(result.get_mpz_t(), numerator.get_mpz_t(), denominator.get_mpz_t()); return result;
}
inline Domain normalized(Domain domain) {
  if (domain.lower && domain.upper && *domain.lower > *domain.upper) domain.values = std::vector<Int>{};
  if (domain.values) {
    auto& values = *domain.values;
    values.erase(std::remove_if(values.begin(), values.end(), [&](const Int& value) {
      return (domain.lower && value < *domain.lower) || (domain.upper && value > *domain.upper);
    }), values.end());
    std::sort(values.begin(), values.end());
    values.erase(std::unique(values.begin(), values.end()), values.end());
  }
  return domain;
}
inline Domain intersection(Domain left, const Domain& right) {
  if (right.lower && (!left.lower || *right.lower > *left.lower)) left.lower = right.lower;
  if (right.upper && (!left.upper || *right.upper < *left.upper)) left.upper = right.upper;
  if (left.values) {
    auto& values = *left.values;
    values.erase(std::remove_if(values.begin(), values.end(), [&](const Int& value) {
      return !contains(right, value);
    }), values.end());
  } else if (right.values) left.values = right.values;
  return normalized(std::move(left));
}
inline DomainResult invert_domain(const Domain& domain, const Member& member, const Limits& limits = {}) {
  if (domain.values && domain.values->size() > 2) return {Status::unsupported, {}};
  if (bits(member.a) > limits.coefficient_bits || bits(member.b) > limits.coefficient_bits ||
      (domain.lower && bits(*domain.lower) > limits.input_bits) ||
      (domain.upper && bits(*domain.upper) > limits.input_bits)) return {Status::budget, {}};
  if (domain.values) for (const auto& value : *domain.values)
    if (bits(value) > limits.input_bits) return {Status::budget, {}};
  Domain result;
  if (member.a == 0) {
    if (!contains(domain, member.b)) result.values = std::vector<Int>{};
    return {Status::ok, result};
  }
  const Int negative_offset = -member.b;
  if ((domain.lower && !sum_fits(*domain.lower, negative_offset, limits.intermediate_bits)) ||
      (domain.upper && !sum_fits(*domain.upper, negative_offset, limits.intermediate_bits)))
    return {Status::budget, {}};
  if (domain.values) for (const auto& value : *domain.values)
    if (!sum_fits(value, negative_offset, limits.intermediate_bits)) return {Status::budget, {}};
  auto quotient = [&](const Int& bound, bool lower) {
    Int numerator = bound - member.b;
    return lower ? ceil_div(numerator, member.a) : floor_div(numerator, member.a);
  };
  if (member.a > 0) {
    if (domain.lower) result.lower = quotient(*domain.lower, true);
    if (domain.upper) result.upper = quotient(*domain.upper, false);
  } else {
    if (domain.upper) result.lower = quotient(*domain.upper, true);
    if (domain.lower) result.upper = quotient(*domain.lower, false);
  }
  if (domain.values) {
    result.values = std::vector<Int>{};
    for (const auto& value : *domain.values) {
      auto root = inverse(member, value);
      if (root) result.values->push_back(*root);
    }
  }
  result = normalized(std::move(result));
  if ((result.lower && bits(*result.lower) > limits.intermediate_bits) ||
      (result.upper && bits(*result.upper) > limits.intermediate_bits)) return {Status::budget, {}};
  if (result.values) for (const auto& value : *result.values)
    if (bits(value) > limits.intermediate_bits) return {Status::budget, {}};
  return {Status::ok, result};
}
inline BuildResult build_views(const std::vector<Row>& rows, const std::vector<Domain>& domains,
                               const Limits& limits = {}) {
  BuildResult result;
  std::vector<std::size_t> parents(domains.size());
  for (std::size_t i = 0; i < parents.size(); ++i) parents[i] = i;
  auto find = [&](std::size_t i) {
    while (parents[i] != i) { parents[i] = parents[parents[i]]; i = parents[i]; }
    return i;
  };
  for (const auto& row : rows) {
    // Invalid input must not silently leave an accepted partial component.
    if (row.first >= domains.size() || row.second >= domains.size() || row.first == row.second ||
        row.first_coefficient == 0 || row.second_coefficient == 0) {
      result.skip_unsupported = 1; return result;
    }
    parents[find(row.second)] = find(row.first);
  }
  std::map<std::size_t, std::vector<const Row*>> components;
  for (const auto& row : rows) components[find(row.first)].push_back(&row);
  struct Edge { std::size_t child; Int parent_coefficient, child_coefficient, constant; std::size_t source_id; };
  for (const auto& entry : components) {
    ++result.components;
    std::set<std::size_t> names;
    std::map<std::size_t, std::vector<Edge>> edges;
    bool input_budget = false;
    for (const Row* row : entry.second) {
      names.insert(row->first); names.insert(row->second);
      input_budget |= bits(row->first_coefficient) > limits.input_bits ||
                      bits(row->second_coefficient) > limits.input_bits || bits(row->constant) > limits.input_bits;
    }
    if (names.size() > limits.members) { ++result.skip_size; continue; }
    if (input_budget) { ++result.skip_bits; continue; }
    // Global input counts / total BFS work are bounded by the installing core.
    // This is a per-component row cap, not a total build-work claim.
    if (entry.second.size() > limits.work) { ++result.skip_unsupported; continue; }
    for (const Row* row : entry.second) {
      if (row->second_coefficient == 1 || row->second_coefficient == -1)
        edges[row->first].push_back({row->second,row->first_coefficient,row->second_coefficient,row->constant,row->source_id});
      if (row->first_coefficient == 1 || row->first_coefficient == -1)
        edges[row->second].push_back({row->first,row->second_coefficient,row->first_coefficient,row->constant,row->source_id});
    }
    bool root_exists = false, installed = false, domain_failed = false;
    for (std::size_t root : names) {
      std::set<std::size_t> reached{root}; std::deque<std::size_t> queue{root};
      while (!queue.empty()) {
        std::size_t parent = queue.front(); queue.pop_front();
        for (const auto& edge : edges[parent]) if (reached.insert(edge.child).second) queue.push_back(edge.child);
      }
      if (reached.size() != names.size()) continue;
      root_exists = true;
      std::map<std::size_t, Member> members{{root, {root, 1, 0}}};
      std::vector<std::size_t> tree;
      queue = {root}; bool budget = false;
      while (!queue.empty() && !budget) {
        std::size_t parent = queue.front(); queue.pop_front();
        for (const auto& edge : edges[parent]) if (!members.count(edge.child)) {
          const Member& pm = members.at(parent);
          if (!product_fits(edge.parent_coefficient, pm.a, limits.intermediate_bits) ||
              !product_fits(edge.parent_coefficient, pm.b, limits.intermediate_bits)) { budget = true; break; }
          Int a = edge.parent_coefficient * pm.a;
          Int b = edge.parent_coefficient * pm.b;
          if (!sum_fits(b, edge.constant, limits.intermediate_bits)) { budget = true; break; }
          b += edge.constant;
          if (edge.child_coefficient == 1) { a = -a; b = -b; }
          if (bits(a) > limits.coefficient_bits || bits(b) > limits.coefficient_bits) { budget = true; break; }
          members.emplace(edge.child, Member{edge.child,a,b}); tree.push_back(edge.source_id); queue.push_back(edge.child);
        }
      }
      if (budget) continue;
      View view; view.root = root; view.selected_rows = std::move(tree);
      Status domain_status = Status::ok;
      for (const auto& member : members) {
        view.members.push_back(member.second);
        auto pulled = invert_domain(domains[member.first], member.second, limits);
        if (pulled.status != Status::ok) { domain_status = pulled.status; break; }
        view.domain = intersection(std::move(view.domain), pulled.domain);
      }
      if (domain_status != Status::ok) {
        if (domain_status == Status::budget) ++result.skip_bits; else ++result.skip_unsupported;
        domain_failed = true; break;
      }
      if (!project(view.domain, Int(0))) { ++result.skip_domain; domain_failed = true; break; }
      result.views.push_back(std::move(view)); installed = true; break;
    }
    if (!installed && !domain_failed) {
      if (root_exists) ++result.skip_bits; else ++result.skip_no_root;
    }
  }
  return result;
}
inline PolyResult expand_product(const Int& coefficient, const std::vector<Factor>& factors,
                                 const Limits& limits, std::size_t& work) {
  auto failed = [](Status status) { return PolyResult{status, {}}; };
  if (bits(coefficient) > limits.input_bits) return failed(Status::budget);
  std::size_t degree = 0;
  for (const auto& factor : factors) {
    if (work >= limits.work) return failed(Status::budget);
    ++work;
    if (factor.exponent > limits.exponent) return failed(Status::unsupported);
    if (bits(factor.a) > limits.input_bits || bits(factor.b) > limits.input_bits) return failed(Status::budget);
    if (factor.a != 0) {
      if (factor.exponent > limits.degree || degree > limits.degree - factor.exponent) return failed(Status::unsupported);
      degree += factor.exponent;
    }
  }
  std::vector<Int> polynomial{coefficient};
  for (const auto& factor : factors) for (unsigned exponent = 0; exponent < factor.exponent; ++exponent) {
    std::vector<Int> term{factor.b};
    if (factor.a != 0) term.push_back(factor.a);
    std::vector<Int> product(polynomial.size() + term.size() - 1, 0);
    for (std::size_t i = 0; i < polynomial.size(); ++i) for (std::size_t j = 0; j < term.size(); ++j) {
      if (work > limits.work || limits.work - work < 2) return failed(Status::budget);
      work += 2;
      if (!product_fits(polynomial[i], term[j], limits.intermediate_bits)) return failed(Status::budget);
      Int value = polynomial[i] * term[j];
      if (bits(value) > limits.intermediate_bits) return failed(Status::budget);
      if (!sum_fits(product[i+j], value, limits.intermediate_bits)) return failed(Status::budget);
      product[i+j] += value;
      if (bits(product[i+j]) > limits.intermediate_bits) return failed(Status::budget);
    }
    while (product.size() > 1 && product.back() == 0) product.pop_back();
    polynomial = std::move(product);
  }
  if (bits(polynomial.front()) > limits.intermediate_bits || work > limits.work) return failed(Status::budget);
  return {Status::ok, std::move(polynomial)};
}
}  // namespace nia::affine
