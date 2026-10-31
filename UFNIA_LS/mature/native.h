#pragma once
#include "nia_ls.h"
namespace nia {
using Monomial=std::vector<std::string>;
using Polynomial=std::map<Monomial,Int>;
struct NativeAtom { bool arithmetic=false,equality=false; std::string boolean; Polynomial polynomial; };
struct NativeUfName {bool arithmetic;std::string name;};
struct NativeUfApplication {unsigned function;std::vector<NativeUfName> arguments;NativeUfName result;};
struct NativeInput {
 std::vector<NativeUfApplication> uf_applications;
 std::vector<NativeAtom> atoms;
 std::vector<std::vector<int>> clauses;
 std::map<std::string,Int> restored;
 size_t max_numeral_bits=0;
 void read(const std::string& path,bool allow_uf=false);
 void install(ls_solver& solver) const;
 bool validate(const ls_solver& solver) const;
};
std::string json_string(const std::string& input);
}
