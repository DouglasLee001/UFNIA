// Exact adapter into the mature NIA term/literal/watch kernel. Internal
// elimination and auxiliary solvers are intentionally absent; original CNF is
// retained independently for final validation and complete named model output.
#include "native.h"
#include <cctype>
#include <set>
#include <sstream>
#include <stdexcept>
namespace nia {
namespace {
std::vector<std::string> tokenize(const std::string& text){
 std::vector<std::string> result;std::string token;
 auto flush=[&](){if(!token.empty()){result.push_back(token);token.clear();}};
 for(char c:text){if(std::isspace(static_cast<unsigned char>(c)))flush();else if(c=='('||c==')'){flush();result.emplace_back(1,c);}else token+=c;}flush();return result;
}
bool numeral(const std::string& token){size_t i=token.size()>0&&(token[0]=='-'||token[0]=='+')?1:0;if(i==token.size())return false;for(;i<token.size();++i)if(!std::isdigit(static_cast<unsigned char>(token[i])))return false;return true;}
Int number(const std::string& text,size_t& bits){if(!numeral(text))throw std::runtime_error("invalid integer: "+text);Int n;if(n.set_str(text[0]=='+'?text.substr(1):text,10))throw std::runtime_error("invalid integer");bits=std::max(bits,n==0?size_t(0):mpz_sizeinbase(n.get_mpz_t(),2));return n;}
int index_number(const std::string& text){size_t unused=0;Int n=number(text,unused);if(!n.fits_sint_p())throw std::runtime_error("index/count out of range");return n.get_si();}
Polynomial add(Polynomial a,const Polynomial& b,int sign=1){for(const auto& [m,c]:b){a[m]+=sign*c;if(a[m]==0)a.erase(m);}return a;}
Polynomial multiply(const Polynomial& a,const Polynomial& b){Polynomial r;for(const auto& [ma,ca]:a)for(const auto& [mb,cb]:b){Monomial m=ma;m.insert(m.end(),mb.begin(),mb.end());std::sort(m.begin(),m.end());r[m]+=ca*cb;if(r[m]==0)r.erase(m);}return r;}
struct Reader {
 const std::vector<std::string>& tokens;size_t pos=0;size_t& bits;
 std::string pop(){if(pos==tokens.size())throw std::runtime_error("unexpected end of expression");return tokens[pos++];}
 Polynomial polynomial(){std::string t=pop();if(t!="("){
   if(numeral(t)){Int v=number(t,bits);return v==0?Polynomial{}:Polynomial{{{},v}};}
   if(t.empty()||std::isdigit(static_cast<unsigned char>(t[0]))||t==")"||t=="+"||t=="*"||t=="-"||t=="=")throw std::runtime_error("invalid arithmetic token: "+t);
   return {{{t},Int(1)}};
  }
  std::string op=pop();std::vector<Polynomial> args;
  while(pos<tokens.size()&&tokens[pos]!=")")args.push_back(polynomial());
  if(pop()!=")")throw std::runtime_error("missing close parenthesis");
  if(op=="+"){Polynomial r;for(const auto& a:args)r=add(std::move(r),a);return r;}
  if(op=="*"){Polynomial r{{{},Int(1)}};for(const auto& a:args)r=multiply(r,a);return r;}
  if(op=="-"&&!args.empty()){if(args.size()==1)return add({},args[0],-1);Polynomial r=args[0];for(size_t i=1;i<args.size();++i)r=add(std::move(r),args[i],-1);return r;}
  throw std::runtime_error("unsupported polynomial operator: "+op);
 }
};
std::string required_line(std::istream& stream){std::string line;if(!std::getline(stream,line))throw std::runtime_error("truncated native input");return line;}
}
void NativeInput::read(const std::string& path,bool allow_uf){
 std::ifstream stream(path);if(!stream)throw std::runtime_error("cannot open native input");
 int count=index_number(required_line(stream));if(count<1)throw std::runtime_error("invalid atom count");atoms.resize(count);
 std::vector<bool> defined(count,false);
 for(int i=1;i<count;++i){auto tokens=tokenize(required_line(stream));if(tokens.size()<2)throw std::runtime_error("missing atom body");int id=index_number(tokens[0]);if(id<=0||id>=count||defined[id])throw std::runtime_error("invalid/duplicate atom index");defined[id]=true;tokens.erase(tokens.begin());
  NativeAtom atom;
  if(tokens.size()==1){atom.boolean=tokens[0];if(numeral(atom.boolean)||atom.boolean=="("||atom.boolean==")")throw std::runtime_error("invalid Boolean symbol");}
  else {Reader reader{tokens,0,max_numeral_bits};if(reader.pop()!="(")throw std::runtime_error("expected arithmetic atom");std::string rel=reader.pop();if(rel!="="&&rel!="<="&&rel!="<"&&rel!=">="&&rel!=">")throw std::runtime_error("unsupported atom relation");
   auto lhs=reader.polynomial(),rhs=reader.polynomial();if(reader.pop()!=")"||reader.pos!=tokens.size())throw std::runtime_error("trailing arithmetic tokens");atom.arithmetic=true;atom.equality=rel=="=";
   atom.polynomial=rel==">"||rel==">="?add(std::move(rhs),lhs,-1):add(std::move(lhs),rhs,-1);
   if(rel=="<"||rel==">")atom.polynomial=add(std::move(atom.polynomial),{{{},Int(1)}});
  }atoms[id]=std::move(atom);
 }
 if(index_number(required_line(stream))!=0)throw std::runtime_error("unexpected atom delimiter");
 count=index_number(required_line(stream));if(count<0)throw std::runtime_error("negative clause count");
 for(int i=0;i<count;++i){auto tokens=tokenize(required_line(stream));if(tokens.size()<2||tokens.front()!="("||tokens.back()!=")")throw std::runtime_error("invalid CNF clause");std::vector<int> clause;
  std::set<int> seen;bool tautology=false;
  for(size_t j=1;j+1<tokens.size();++j){int id=index_number(tokens[j]);if(id==0||id==INT_MIN||size_t(std::abs(id))>=atoms.size())throw std::runtime_error("clause literal index out of range");if(seen.count(-id))tautology=true;if(seen.insert(id).second)clause.push_back(id);}
  // Removing an explicit complementary pair is exact, and avoids duplicated
  // Boolean watch occurrences. The original semantic clause is tautological.
  if(!tautology)clauses.push_back(std::move(clause));
 }
 count=index_number(required_line(stream));if(count<0)throw std::runtime_error("negative restored-value count");
 for(int i=0;i<count;++i){auto tokens=tokenize(required_line(stream));if(tokens.size()!=2||restored.count(tokens[0]))throw std::runtime_error("invalid restored value");restored[tokens[0]]=number(tokens[1],max_numeral_bits);}
 std::string extra;std::vector<std::string> header;
 while(std::getline(stream,extra)){header=tokenize(extra);if(!header.empty())break;}
 if(allow_uf&&header.empty())throw std::runtime_error("missing UFNIA_OBSERVERS table");
 if(!header.empty()){
  if(!allow_uf||header.size()!=3||header[0]!="UFNIA_OBSERVERS"||header[1]!="1")throw std::runtime_error("unexpected native UF tail");
  int count=index_number(header[2]);if(count<0)throw std::runtime_error("negative UF count");
  std::map<unsigned,std::vector<bool>> signatures;
  for(int row=0;row<count;++row){auto tokens=tokenize(required_line(stream));if(tokens.size()<4)throw std::runtime_error("truncated UF application");int fid=index_number(tokens[0]),arity=index_number(tokens[1]);if(fid<0||arity<0||size_t(arity)>(tokens.size()-4)/2||tokens.size()!=size_t(4+2*arity))throw std::runtime_error("invalid UF application arity/id");
   auto name=[&](size_t i){if(tokens[i]!="Int"&&tokens[i]!="Bool")throw std::runtime_error("invalid UF sort");const auto& value=tokens[i+1];if(value.empty()||numeral(value)||std::isdigit(static_cast<unsigned char>(value[0]))||value=="("||value==")")throw std::runtime_error("invalid UF variable name");return NativeUfName{tokens[i]=="Int",value};};
   NativeUfApplication app;app.function=fid;std::vector<bool> signature;for(int i=0;i<arity;++i){app.arguments.push_back(name(2+2*i));signature.push_back(app.arguments.back().arithmetic);}app.result=name(2+2*arity);signature.push_back(app.result.arithmetic);auto prior=signatures.emplace(fid,signature);if(!prior.second&&prior.first->second!=signature)throw std::runtime_error("inconsistent UF declaration signature");uf_applications.push_back(std::move(app));
  }
  while(std::getline(stream,extra))if(!tokenize(extra).empty())throw std::runtime_error("trailing native UF input");
 }

}
void NativeInput::install(ls_solver& s) const {
 s._num_lits=atoms.size();s._lits.resize(atoms.size());s._num_vars=0;s._num_nia_vars=0;s._num_nia_lits=0;s._num_bool_lits=0;s._step=0;s._outer_layer_step=0;
 auto variable_id=[&](const std::string& name,bool arithmetic){auto found=s.name2var.find(name);if(found!=s.name2var.end()){if(s._vars[found->second].is_nia!=arithmetic)throw std::runtime_error("mixed Boolean/integer symbol: "+name);return found->second;}size_t id=s._vars.size();variable v{};v.var_name=name;v.is_nia=arithmetic;v.score=0;s._vars.push_back(v);s.name2var[name]=id;if(arithmetic)s.nia_var_vec.push_back(id);else s.bool_var_vec.push_back(id);return id;};
 std::map<Monomial,size_t> term_ids;
 std::map<std::string,int> boolean_ids;std::vector<int> canonical(atoms.size());for(size_t i=0;i<canonical.size();++i)canonical[i]=i;
 for(size_t id=1;id<atoms.size();++id){const auto& atom=atoms[id];lit& l=s._lits[id];l.lits_index=id;l.key=0;l.delta=0;l.is_true=-1;l.is_nia_lit=atom.arithmetic;l.is_equal=atom.equality;
  if(!atom.arithmetic){l.bool_var=variable_id(atom.boolean,false);auto prior=boolean_ids.emplace(atom.boolean,id);if(prior.second){s._vars[l.bool_var].literal_idxs.push_back(id);++s._num_bool_lits;}else{canonical[id]=prior.first->second;l.lits_index=0;}continue;}
  ++s._num_nia_lits;
  for(const auto& [monomial,coefficient]:atom.polynomial){if(monomial.empty()){l.key+=coefficient;continue;}size_t tid;auto found=term_ids.find(monomial);
   if(found!=term_ids.end())tid=found->second;else{tid=s._terms.size();term_ids[monomial]=tid;term t{};for(const auto& name:monomial){size_t v=variable_id(name,true);auto existing=std::find_if(t.var_epxs.begin(),t.var_epxs.end(),[&](const var_exp& e){return e.var_index==int(v);});if(existing==t.var_epxs.end())t.var_epxs.emplace_back(v);else ++existing->exponent;}s._terms.push_back(std::move(t));}
   l.coff_terms.emplace_back(tid,coefficient);
   for(auto& ve:s._terms[tid].var_epxs){var_lit_term vlt(ve,tid,id,coefficient);l.var_lit_terms.push_back(vlt);s._vars[ve.var_index].var_lit_terms.push_back(vlt);++s._num_opt;}
  }
  std::sort(l.var_lit_terms.begin(),l.var_lit_terms.end(),[](const auto& a,const auto& b){return a.var_idx<b.var_idx||(a.var_idx==b.var_idx&&(a.exponent<b.exponent||(a.exponent==b.exponent&&a.term_idx<b.term_idx)));});
 }
 for(const auto& original:clauses){std::vector<int> input;std::set<int> seen;bool tautology=false;for(int li:original){int mapped=(li>0?1:-1)*canonical[std::abs(li)];if(seen.count(-mapped))tautology=true;if(seen.insert(mapped).second)input.push_back(mapped);}if(tautology)continue;clause c{};c.literals=input;size_t cid=s._clauses.size();if(input.empty())s.build_unsat=true;for(int li:input){const auto& l=s._lits[std::abs(li)];if(l.is_nia_lit){c.nia_literals.push_back(li);for(const auto& vlt:l.var_lit_terms)s._vars[vlt.var_idx].clause_idxs.push_back(cid);}else{c.bool_literals.push_back(li);s._vars[l.bool_var].clause_idxs.push_back(cid);s._vars[l.bool_var].bool_var_in_pos_clause.push_back(li>0);}}s._clauses.push_back(std::move(c));}
 for(const auto& app:uf_applications){ls_solver::UfApplication record;record.function=app.function;for(const auto& argument:app.arguments)record.arguments.push_back(variable_id(argument.name,argument.arithmetic));record.result=variable_id(app.result.name,app.result.arithmetic);s.uf_applications.push_back(std::move(record));}
 s._num_vars=s._vars.size();s._num_nia_vars=s.nia_var_vec.size();s._num_clauses=s._clauses.size();s.term_appear.assign(s._terms.size(),true);s.lit_appear.assign(s._lits.size(),true);
 s.var_in_long_term=new Array(s._num_vars+s._additional_len);
 for(size_t t=0;t<s._terms.size();++t)for(const auto& ve:s._terms[t].var_epxs){s._vars[ve.var_index].term_exps.emplace_back(t,ve.exponent);if(ve.exponent>1||s._terms[t].var_epxs.size()>2)s.var_in_long_term->insert_element(ve.var_index);}
 for(auto& v:s._vars){if(v.is_nia){std::sort(v.clause_idxs.begin(),v.clause_idxs.end());v.clause_idxs.erase(std::unique(v.clause_idxs.begin(),v.clause_idxs.end()),v.clause_idxs.end());for(const auto& vlt:v.var_lit_terms)if(v.literal_idxs.empty()||v.literal_idxs.back()!=vlt.lit_idx)v.literal_idxs.push_back(vlt.lit_idx);}v.scoring_coffs.resize(v.var_lit_terms.size());}

 for(const auto& c:s._clauses){
  if(c.literals.size()!=1)continue;int li=c.literals[0];const lit& l=s._lits[std::abs(li)];
  if(!l.is_nia_lit||l.coff_terms.size()!=1)continue;const auto& ct=l.coff_terms[0];const auto& t=s._terms[ct.term_idx];
  if(t.var_epxs.size()!=1||t.var_epxs[0].exponent!=1||ct.coff==0)continue;
  auto& v=s._vars[t.var_epxs[0].var_index];Int a=ct.coff,k=l.key;
  auto lower=[&](const Int& n){if(!v.low_bound||n>*v.low_bound)v.low_bound=n;};
  auto upper=[&](const Int& n){if(!v.upper_bound||n<*v.upper_bound)v.upper_bound=n;};
  if(l.is_equal){if(li>0&&k%a==0){Int n=-k/a;lower(n);upper(n);}continue;}
  if(li<0){a=-a;k=1-k;}Int numerator=-k,n;
  if(a>0){mpz_fdiv_q(n.get_mpz_t(),numerator.get_mpz_t(),a.get_mpz_t());upper(n);}
  else{mpz_cdiv_q(n.get_mpz_t(),numerator.get_mpz_t(),a.get_mpz_t());lower(n);}
 }
 s.make_space();s.set_pre_value();if(s.uf_enabled)s.prepare_uf();if(s.affine_enabled)s.prepare_affine_views();
}
bool NativeInput::validate(const ls_solver& s) const {
 std::vector<bool> truth(atoms.size(),false);
 for(size_t i=1;i<atoms.size();++i){const auto& atom=atoms[i];if(!atom.arithmetic){truth[i]=s._solution.at(s.name2var.at(atom.boolean))>0;continue;}Int residual=0;for(const auto& [m,c]:atom.polynomial){Int value=c;for(const auto& name:m)value*=s._solution.at(s.name2var.at(name));residual+=value;}truth[i]=atom.equality?residual==0:residual<=0;}
 for(const auto& clause:clauses){bool satisfied=false;for(int li:clause)if(truth[std::abs(li)]==(li>0)){satisfied=true;break;}if(!satisfied)return false;}return !s.uf_enabled||s.validate_uf();
}
void ls_solver::make_space(){
 _solution.resize(_num_vars+_additional_len);_best_solutin.resize(_num_vars+_additional_len);tabulist.resize(2*_num_vars+_additional_len,0);last_move.resize(2*_num_vars+_additional_len,0);
 operation_var_idx_vec.resize(3*_num_opt+_additional_len);operation_change_value_vec.resize(3*_num_opt+_additional_len);operation_var_idx_bool_vec.resize(_num_vars+_additional_len);is_chosen_bool_var.resize(_num_vars+_additional_len,false);
 unsat_clauses=new Array(_num_clauses+_additional_len);sat_clause_with_false_literal=new Array(_num_clauses+_additional_len);false_lit_occur=new Array(_num_lits+_additional_len);contain_bool_unsat_clauses=new Array(_num_clauses+_additional_len);
 _lit_make_break.resize(_num_lits+_additional_len,0);term_coffs.resize(_terms.size()+_additional_len,0);initialize_scoring_index();_best_literal_assignment.resize(_num_lits+_additional_len);_frequency_in_unsat_clauses.resize(_num_lits+_additional_len,0);is_in_unsat_clause.resize(_num_lits+_additional_len,false);lits_in_unsat_clause.resize(_num_lits+_additional_len);
}
void ls_solver::set_pre_value(){
 _pre_value_1.resize(_num_vars);_pre_value_2.resize(_num_vars);
 auto simple=[&](int index,size_t& var,Int& value){if(index<=0)return false;const lit& l=_lits[index];if(!l.is_nia_lit||!l.is_equal||l.coff_terms.size()!=1)return false;const auto& ct=l.coff_terms[0];const auto& t=_terms[ct.term_idx];if(t.var_epxs.size()!=1||t.var_epxs[0].exponent!=1||ct.coff==0||l.key%ct.coff!=0)return false;var=t.var_epxs[0].var_index;value=-l.key/ct.coff;return true;};
 for(const auto& c:_clauses){size_t a,b;Int first,second;if(c.literals.size()==1&&simple(c.literals[0],a,first))_pre_value_1[a]=first;else if(c.literals.size()==2&&simple(c.literals[0],a,first)&&simple(c.literals[1],b,second)&&a==b&&!_pre_value_1[a]){_pre_value_1[a]=first;_pre_value_2[a]=second;}}
}
void ls_solver::up_bool_vars(){} // No variables are eliminated in this port.
bool ls_solver::try_joint_move(){return false;} // Optional legacy auxiliary disabled.
void ls_solver::prepare_restart_repair(){} // Auxiliary repair solver disabled.
std::string ls_solver::print_128(Int n){return n.get_str();}
ls_solver::~ls_solver(){delete unsat_clauses;delete sat_clause_with_false_literal;delete contain_bool_unsat_clauses;delete false_lit_occur;delete var_in_long_term;}
std::string json_string(const std::string& input){std::string out="\"";const char* hex="0123456789abcdef";for(unsigned char c:input){if(c=='"'||c=='\\'){out+='\\';out+=c;}else if(c<32){out+="\\u00";out+=hex[c>>4];out+=hex[c&15];}else out+=c;}return out+'"';}
}
